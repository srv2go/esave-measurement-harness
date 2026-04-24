#!/usr/bin/env python3
"""
Notebook Output Bridge v3.0 — Local-First Edition
===================================================
DTS/ESAVE Contribution to D&A Team

Executes, parses, and renders Jupyter notebook outputs as rich HTML
dashboards — entirely local, no server process required.

Dependencies (all ship with Jupyter):
  - nbformat        (notebook read/write)
  - nbclient        (notebook execution)
  - nbparameterise  (parameter injection — lightweight, no Papermill)
  - nbconvert       (optional, for fallback HTML export)

NO Flask. NO Gradio. NO Streamlit. NO HTTP ports.

Two delivery modes:
  Tier 1 — Static file:    Generates dashboard.html on disk
  Tier 2 — Jupyter-native: Renders dashboard inside a notebook cell
                           via IPython.display.HTML()

Usage:
  # Tier 1: Generate static dashboard
  python notebook_bridge.py render -n executed.ipynb -o dashboard.html

  # Tier 1: Execute + render in one step
  python notebook_bridge.py run -n template.ipynb \\
      --params '{"spec_path": "/data/spec.pdf"}' -o report.html

  # Extract structured data as JSON
  python notebook_bridge.py extract -n executed.ipynb

  # Check environment readiness
  python notebook_bridge.py check

  # Tier 2: Use from inside a Jupyter notebook (see harness_notebook.ipynb)
  #   from notebook_bridge import run_and_display
  #   run_and_display("template.ipynb", params={"spec_path": "..."})
"""

import json
import os
import re
import sys
import copy
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from html import escape as html_escape


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 1: Environment Check
# ═══════════════════════════════════════════════════════════════════════════

REQUIRED_PACKAGES = {
    'nbformat': 'nbformat',
    'nbclient': 'nbclient',
    'nbparameterise': 'nbparameterise',
}

OPTIONAL_PACKAGES = {
    'nbconvert': 'nbconvert (optional — for fallback HTML export)',
    'IPython': 'IPython (for Tier 2 — Jupyter-native display)',
}


def check_environment() -> Dict[str, bool]:
    """Check which packages are available and report readiness."""
    results = {}
    print("Notebook Output Bridge v3.0 — Environment Check")
    print("=" * 52)

    print("\nRequired packages:")
    all_ok = True
    for pkg, label in REQUIRED_PACKAGES.items():
        try:
            mod = __import__(pkg)
            ver = getattr(mod, '__version__', '?')
            print(f"  ✓ {label} ({ver})")
            results[pkg] = True
        except ImportError:
            print(f"  ✗ {label} — install with: pip install {pkg}")
            results[pkg] = False
            all_ok = False

    print("\nOptional packages:")
    for pkg, label in OPTIONAL_PACKAGES.items():
        try:
            mod = __import__(pkg)
            ver = getattr(mod, '__version__', '?')
            print(f"  ✓ {label} ({ver})")
            results[pkg] = True
        except ImportError:
            print(f"  ○ {label} — not found (not required)")
            results[pkg] = False

    print(f"\nTier 1 (static file render):   {'READY' if all_ok else 'MISSING DEPS'}")
    print(f"Tier 2 (Jupyter-native):       {'READY' if all_ok and results.get('IPython') else 'READY (when run inside Jupyter)'}")
    print(f"Tier 3 (Flask server):          NOT INCLUDED — governance restricted\n")

    if not all_ok:
        missing = [pkg for pkg, ok in results.items() if not ok and pkg in REQUIRED_PACKAGES]
        print(f"Install missing: pip install {' '.join(missing)}")

    return results


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 2: Notebook Execution Engine
# ═══════════════════════════════════════════════════════════════════════════

class NotebookExecutor:
    """
    Execute Jupyter notebooks programmatically with parameter injection.

    Uses nbparameterise for parameter injection (lightweight alternative
    to Papermill — does not bring in the full execution stack).
    Uses nbclient for execution (ships with Jupyter).

    Parameter injection flow:
    1. Read the template notebook
    2. nbparameterise extracts parameters from the tagged cell
    3. Override with user-supplied values
    4. nbclient executes the full notebook through the kernel
    5. Save executed notebook with all outputs
    """

    def __init__(self, kernel_name: str = 'python3', timeout: int = 600,
                 on_cell_error: str = 'continue'):
        """
        Args:
            kernel_name:   Jupyter kernel to use
            timeout:       Max seconds per cell
            on_cell_error: 'continue' to keep going on failure (captures
                          partial results), 'raise' to stop immediately
        """
        self.kernel_name = kernel_name
        self.timeout = timeout
        self.on_cell_error = on_cell_error

    def execute(
        self,
        notebook_path: str,
        output_path: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Execute a notebook, optionally with parameter overrides.

        Args:
            notebook_path: Path to the .ipynb template
            output_path:   Where to save executed notebook
                          (default: <stem>_executed.ipynb)
            parameters:    Dict of parameter overrides

        Returns:
            Path to the executed notebook with all outputs populated
        """
        import nbformat
        from nbclient import NotebookClient

        if output_path is None:
            stem = Path(notebook_path).stem
            parent = Path(notebook_path).parent
            output_path = str(parent / f"{stem}_executed.ipynb")

        # Read template
        _log(f"Reading template: {notebook_path}")
        with open(notebook_path, 'r', encoding='utf-8') as f:
            nb = nbformat.read(f, as_version=4)

        # Inject parameters via nbparameterise
        if parameters:
            nb = self._inject_parameters(nb, parameters)

        # Execute via nbclient
        _log(f"Executing (kernel={self.kernel_name}, timeout={self.timeout}s, "
             f"on_error={self.on_cell_error})...")

        client = NotebookClient(
            nb,
            timeout=self.timeout,
            kernel_name=self.kernel_name,
            resources={'metadata': {'path': str(Path(notebook_path).parent)}},
        )

        # Set error handling policy
        if self.on_cell_error == 'continue':
            client.allow_errors = True

        try:
            client.execute()
            _log("Execution complete — all cells finished")
        except Exception as e:
            _log(f"Execution error (partial results saved): {e}")

        # Save executed notebook
        with open(output_path, 'w', encoding='utf-8') as f:
            nbformat.write(nb, f)

        _log(f"Saved: {output_path}")
        return output_path

    def _inject_parameters(self, nb, parameters: Dict[str, Any]):
        """
        Inject parameters using nbparameterise.

        nbparameterise reads the cell tagged 'parameters', extracts
        the declared variables, and replaces their values. This is
        the correct tool when Papermill is blocked — it handles
        parameter injection without bringing in execution machinery.
        """
        try:
            from nbparameterise import extract_parameters, parameter_values, replace_definitions

            _log(f"Injecting parameters: {list(parameters.keys())}")

            # Extract declared parameters from the tagged cell
            orig_params = extract_parameters(nb)

            # Build new parameter values
            new_params = parameter_values(orig_params, **parameters)

            # Replace in notebook
            nb = replace_definitions(nb, new_params)

            return nb

        except ImportError:
            _log("WARNING: nbparameterise not available — using manual injection")
            return self._manual_inject(nb, parameters)

    def _manual_inject(self, nb, parameters: Dict[str, Any]):
        """
        Fallback parameter injection without nbparameterise.
        Finds the 'parameters' tagged cell and inserts an override cell.
        """
        import nbformat

        param_lines = ["# Injected parameters (notebook_bridge — manual fallback)"]
        for key, value in parameters.items():
            param_lines.append(f"{key} = {repr(value)}")

        injected = nbformat.v4.new_code_cell(source="\n".join(param_lines))
        injected.metadata['tags'] = ['injected-parameters']

        # Find parameters cell
        insert_idx = 0
        for idx, cell in enumerate(nb.cells):
            tags = cell.get('metadata', {}).get('tags', [])
            if 'parameters' in tags:
                insert_idx = idx + 1
                break

        nb.cells.insert(insert_idx, injected)
        return nb


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 3: Notebook Parser
# ═══════════════════════════════════════════════════════════════════════════

class NotebookParser:
    """
    Parse .ipynb files and extract structured cell outputs.

    The .ipynb format is JSON. Every cell output — text, HTML tables,
    images — is already serialized in the file. This parser extracts
    and normalizes them for the renderer.

    Bridge markers:
      Notebooks can embed machine-readable data for the dashboard by
      printing it between __BRIDGE_JSON_START/END__ markers. The parser
      extracts this data for structured rendering (stats cards, score
      bars, gap analysis cards).

      Without markers, the parser still extracts all raw cell outputs.
    """

    @staticmethod
    def parse(notebook_path: str) -> Dict[str, Any]:
        """Parse a notebook file into structured data."""
        with open(notebook_path, 'r', encoding='utf-8') as f:
            nb = json.load(f)

        cells = []
        for idx, cell in enumerate(nb.get('cells', [])):
            cell_type = cell.get('cell_type', 'code')
            source = ''.join(cell.get('source', []))
            tags = cell.get('metadata', {}).get('tags', [])

            parsed_cell = {
                'index': idx,
                'type': cell_type,
                'source': source,
                'tags': tags,
                'outputs': []
            }

            if cell_type == 'code':
                for out in cell.get('outputs', []):
                    parsed_output = NotebookParser._parse_output(out)
                    if parsed_output:
                        parsed_cell['outputs'].append(parsed_output)

            cells.append(parsed_cell)

        return {
            'filename': Path(notebook_path).name,
            'path': str(Path(notebook_path).absolute()),
            'parsed_at': datetime.now().isoformat(),
            'kernel': nb.get('metadata', {}).get('kernelspec', {}).get(
                'display_name', 'Unknown'),
            'cell_count': len(nb.get('cells', [])),
            'code_cells': sum(1 for c in cells if c['type'] == 'code'),
            'cells': cells
        }

    @staticmethod
    def _parse_output(out: Dict) -> Optional[Dict]:
        """Parse a single cell output into normalized format."""
        output_type = out.get('output_type', '')

        if output_type == 'stream':
            return {
                'format': 'text',
                'stream': out.get('name', 'stdout'),
                'content': ''.join(out.get('text', []))
            }
        elif output_type in ('execute_result', 'display_data'):
            data = out.get('data', {})
            # Priority: HTML > image > JSON > text
            if 'text/html' in data:
                return {'format': 'html',
                        'content': ''.join(data['text/html'])}
            elif 'image/png' in data:
                return {'format': 'image_base64',
                        'content': data['image/png']}
            elif 'application/json' in data:
                return {'format': 'json',
                        'content': data['application/json']}
            elif 'text/plain' in data:
                return {'format': 'text',
                        'content': ''.join(data['text/plain'])}
        elif output_type == 'error':
            return {
                'format': 'error',
                'ename': out.get('ename', ''),
                'evalue': out.get('evalue', ''),
                'content': '\n'.join(out.get('traceback', []))
            }
        return None

    @staticmethod
    def extract_bridge_json(parsed: Dict) -> Optional[Dict]:
        """Extract structured data from __BRIDGE_JSON_START/END__ markers."""
        for cell in parsed['cells']:
            for output in cell.get('outputs', []):
                if output.get('format') == 'text':
                    match = re.search(
                        r'__BRIDGE_JSON_START__\s*(.*?)\s*__BRIDGE_JSON_END__',
                        output['content'], re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group(1))
                        except json.JSONDecodeError:
                            continue
        return None

    @staticmethod
    def extract_bridge_gaps(parsed: Dict) -> Optional[List]:
        """Extract gap analysis from __BRIDGE_GAPS_START/END__ markers."""
        for cell in parsed['cells']:
            for output in cell.get('outputs', []):
                if output.get('format') == 'text':
                    match = re.search(
                        r'__BRIDGE_GAPS_START__\s*(.*?)\s*__BRIDGE_GAPS_END__',
                        output['content'], re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group(1))
                        except json.JSONDecodeError:
                            continue
        return None

    @staticmethod
    def extract_all_text(parsed: Dict) -> List[str]:
        """Extract all text/stream outputs."""
        return [o['content'] for c in parsed['cells']
                for o in c.get('outputs', []) if o.get('format') == 'text']

    @staticmethod
    def extract_all_html(parsed: Dict) -> List[str]:
        """Extract all HTML outputs (tables, charts)."""
        return [o['content'] for c in parsed['cells']
                for o in c.get('outputs', []) if o.get('format') == 'html']

    @staticmethod
    def extract_summary(parsed: Dict) -> Dict:
        """
        Extract a complete summary combining bridge data, gaps, and
        raw outputs. Useful for downstream consumers (ESAVE agents, etc.)
        """
        bridge = NotebookParser.extract_bridge_json(parsed)
        gaps = NotebookParser.extract_bridge_gaps(parsed)
        return {
            'filename': parsed['filename'],
            'parsed_at': parsed['parsed_at'],
            'bridge_data': bridge,
            'gap_data': gaps,
            'has_structured_data': bridge is not None,
            'cell_count': parsed['cell_count'],
            'code_cells': parsed['code_cells'],
            'output_count': sum(
                len(c.get('outputs', [])) for c in parsed['cells']),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 4: HTML Dashboard Renderer
# ═══════════════════════════════════════════════════════════════════════════

class DashboardRenderer:
    """
    Render parsed notebook data into a self-contained HTML dashboard.

    The output HTML has:
    - All CSS inline (no external dependencies)
    - Dark mode support via prefers-color-scheme
    - Responsive layout
    - Stats cards, score bars, gap analysis cards (when bridge data present)
    - All cell outputs rendered in order

    Two output modes:
    - render_to_file():   Writes HTML to disk → open in any browser
    - render_to_string(): Returns HTML string → pass to IPython.display
    """

    @classmethod
    def render_to_file(cls, notebook_path: str, output_path: str,
                       include_code: bool = False) -> str:
        """
        Tier 1: Render notebook to a standalone HTML file.

        Args:
            notebook_path: Path to the executed .ipynb
            output_path:   Where to write the HTML file
            include_code:  Whether to show source code cells

        Returns:
            Absolute path to the generated HTML file
        """
        html = cls.render_to_string(notebook_path, include_code)
        output_path = str(Path(output_path).absolute())

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        _log(f"Dashboard written: {output_path}")
        return output_path

    @classmethod
    def render_to_string(cls, notebook_path: str,
                         include_code: bool = False) -> str:
        """
        Render notebook to an HTML string.

        Tier 1 uses this to write to file.
        Tier 2 passes this to IPython.display.HTML().
        """
        parsed = NotebookParser.parse(notebook_path)
        bridge_data = NotebookParser.extract_bridge_json(parsed)
        gap_data = NotebookParser.extract_bridge_gaps(parsed)

        parts = [cls._html_head(parsed)]

        # Structured dashboard when bridge data exists
        if bridge_data:
            parts.append(cls._render_stats_cards(bridge_data))
            if bridge_data.get('validation_data'):
                parts.append(cls._render_score_bars(
                    bridge_data['validation_data'],
                    bridge_data.get('threshold', 0.85)))
                parts.append(cls._render_validation_table(
                    bridge_data['validation_data']))

        if gap_data:
            parts.append(cls._render_gap_analysis(gap_data))

        # Cell outputs section
        parts.append(cls._section_divider("Cell outputs"))
        for cell in parsed['cells']:
            rendered = cls._render_cell(cell, include_code)
            if rendered:
                parts.append(rendered)

        parts.append(cls._render_footer(parsed))
        parts.append("</div></body></html>")
        return ''.join(parts)

    # ── HTML Head with self-contained CSS ──────────────────────────────

    @classmethod
    def _html_head(cls, parsed: Dict) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ESAVE Bridge — {html_escape(parsed['filename'])}</title>
<style>
/* ── Base tokens ─────────────────────────────────────────────── */
:root {{
  --bg:#fafaf8;--bg2:#f1efe8;--bg3:#e8e6dc;
  --fg:#2c2c2a;--fg2:#5f5e5a;--fg3:#888780;
  --brd:rgba(0,0,0,0.08);--brd2:rgba(0,0,0,0.12);
  --pass:#0F6E56;--pass-bg:#E1F5EE;--pass-lt:#9FE1CB;
  --review:#854F0B;--review-bg:#FAEEDA;--review-lt:#FAC775;
  --fail:#A32D2D;--fail-bg:#FCEBEB;--fail-lt:#F7C1C1;
  --info:#185FA5;--info-bg:#E6F1FB;
  --purple:#534AB7;--purple-bg:#EEEDFE;
  --r:10px;--rs:6px;
  --mono:'SF Mono','Fira Code','Cascadia Code','Consolas',monospace;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
}}
@media(prefers-color-scheme:dark){{:root{{
  --bg:#1a1a18;--bg2:#252523;--bg3:#2c2c2a;
  --fg:#e8e6dc;--fg2:#b4b2a9;--fg3:#888780;
  --brd:rgba(255,255,255,0.06);--brd2:rgba(255,255,255,0.10);
  --pass-bg:#04342C;--review-bg:#412402;--fail-bg:#501313;
  --info-bg:#042C53;--purple-bg:#26215C;
}}}}

/* ── Reset & layout ──────────────────────────────────────────── */
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--sans);background:var(--bg);color:var(--fg);
  line-height:1.6;padding:28px;max-width:960px;margin:0 auto}}
.wrap{{max-width:960px;margin:0 auto}}

/* ── Header ──────────────────────────────────────────────────── */
.hdr{{padding:0 0 18px;border-bottom:1px solid var(--brd);margin-bottom:22px}}
.hdr-label{{font-size:11px;font-weight:600;letter-spacing:0.08em;
  text-transform:uppercase;color:var(--pass);margin-bottom:4px}}
.hdr h1{{font-size:20px;font-weight:500;line-height:1.3}}
.hdr .meta{{font-size:12px;color:var(--fg3);margin-top:5px}}

/* ── Stats cards ─────────────────────────────────────────────── */
.stats{{display:flex;gap:10px;margin-bottom:22px;flex-wrap:wrap}}
.stat{{flex:1;min-width:130px;padding:16px;border-radius:var(--r);
  background:var(--bg2);border:1px solid var(--brd)}}
.stat-lbl{{font-size:10px;color:var(--fg3);text-transform:uppercase;
  letter-spacing:0.06em;margin-bottom:3px}}
.stat-val{{font-size:26px;font-weight:500;line-height:1.1}}
.stat-sub{{font-size:11px;color:var(--fg3);margin-top:3px}}

/* ── Sections ────────────────────────────────────────────────── */
.sec{{margin-bottom:22px;padding:18px;border-radius:var(--r);
  background:var(--bg2);border:1px solid var(--brd)}}
.sec h3{{font-size:14px;font-weight:500;margin-bottom:12px}}
.sec-div{{font-size:15px;font-weight:500;margin:28px 0 14px;
  padding-top:18px;border-top:1px solid var(--brd)}}

/* ── Score bars ──────────────────────────────────────────────── */
.s-row{{display:flex;align-items:center;gap:8px;margin-bottom:7px}}
.s-lbl{{width:72px;font-size:11px;color:var(--fg2);text-align:right;flex-shrink:0}}
.s-bar{{flex:1;height:7px;background:var(--bg3);border-radius:4px;overflow:hidden}}
.s-fill{{height:100%;border-radius:4px;transition:width 0.5s ease}}
.s-val{{width:40px;font-size:11px;font-weight:500;text-align:right}}
.legend{{display:flex;gap:14px;margin-bottom:12px;font-size:10px;color:var(--fg3);flex-wrap:wrap}}
.leg-i{{display:flex;align-items:center;gap:4px}}
.leg-d{{width:7px;height:7px;border-radius:2px;display:inline-block}}

/* ── Gap cards ───────────────────────────────────────────────── */
.gap{{padding:12px 14px;margin-bottom:8px;border-radius:8px;
  background:var(--bg);border-left:3px solid}}
.gap.crit{{border-left-color:var(--fail)}}
.gap.mod{{border-left-color:var(--review)}}
.gap-hdr{{display:flex;align-items:center;gap:7px;margin-bottom:3px}}
.badge{{padding:2px 9px;border-radius:99px;font-size:10px;font-weight:500;
  display:inline-block;letter-spacing:0.02em}}
.b-crit{{background:var(--fail-bg);color:var(--fail)}}
.b-mod{{background:var(--review-bg);color:var(--review)}}
.b-pass{{background:var(--pass-bg);color:var(--pass)}}
.gap-t{{font-size:13px;font-weight:500;margin-bottom:2px}}
.gap-d{{font-size:11px;color:var(--fg2);line-height:1.5}}
.gap-r{{font-size:11px;color:var(--info);margin-top:3px}}

/* ── Tables ──────────────────────────────────────────────────── */
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:var(--bg3);padding:8px 12px;text-align:left;font-weight:500;
  border-bottom:2px solid var(--brd2)}}
td{{padding:8px 12px;border-bottom:1px solid var(--brd);color:var(--fg2)}}
tr:hover td{{background:var(--bg2)}}

/* ── Cell outputs ────────────────────────────────────────────── */
.c-out{{margin-bottom:10px}}
.c-code{{padding:10px 12px;border-radius:var(--rs) var(--rs) 0 0;
  background:var(--bg3);font-family:var(--mono);font-size:10px;line-height:1.5;
  color:var(--fg3);overflow-x:auto;white-space:pre-wrap;
  border:1px solid var(--brd);border-bottom:1px dashed var(--brd)}}
.c-text{{padding:12px;border-radius:var(--rs);background:var(--bg2);
  font-family:var(--mono);font-size:11px;line-height:1.6;color:var(--fg2);
  overflow-x:auto;white-space:pre-wrap;border:1px solid var(--brd)}}
.c-html{{overflow:auto;border-radius:var(--rs);border:1px solid var(--brd)}}
.c-html table{{margin:0}}
.c-md{{padding:8px 12px;border-radius:var(--rs);
  font-size:13px;font-weight:500;color:var(--fg)}}
.c-err{{padding:12px;border-radius:var(--rs);background:var(--fail-bg);
  font-family:var(--mono);font-size:11px;color:var(--fail);
  overflow-x:auto;white-space:pre-wrap;border:1px solid var(--fail)}}

/* ── Footer ──────────────────────────────────────────────────── */
.pipe{{display:flex;gap:14px;flex-wrap:wrap;padding:12px 16px;
  border-radius:var(--r);font-size:11px;color:var(--fg3);
  background:var(--bg2);border:1px solid var(--brd);margin-top:22px}}
.ftr{{margin-top:16px;padding-top:14px;border-top:1px solid var(--brd);
  font-size:10px;color:var(--fg3);display:flex;justify-content:space-between;
  flex-wrap:wrap;gap:6px}}
</style>
</head>
<body><div class="wrap">
<div class="hdr">
  <div class="hdr-label">ESAVE — Notebook Output Bridge</div>
  <h1>{html_escape(parsed['filename'])}</h1>
  <div class="meta">Kernel: {html_escape(parsed['kernel'])} · \
Cells: {parsed['cell_count']} ({parsed['code_cells']} code) · \
Rendered: {parsed['parsed_at'][:19]}</div>
</div>
"""

    # ── Stats cards ────────────────────────────────────────────────

    @classmethod
    def _render_stats_cards(cls, data: Dict) -> str:
        total = data.get('total_sections', 0)
        pc = data.get('pass_count', 0)
        rc = data.get('review_count', 0)
        fc = data.get('fail_count', 0)
        mean = data.get('mean_score', 0)
        cov = data.get('coverage', 0)
        pct = f"{pc/total*100:.0f}%" if total else "—"
        thr = data.get('threshold', 0.85)

        return f"""<div class="stats">
  <div class="stat"><div class="stat-lbl">Sections</div>
    <div class="stat-val">{total}</div>
    <div class="stat-sub">normative requirements</div></div>
  <div class="stat"><div class="stat-lbl">Pass</div>
    <div class="stat-val" style="color:var(--pass)">{pc}</div>
    <div class="stat-sub">{pct} pass rate</div></div>
  <div class="stat"><div class="stat-lbl">Review</div>
    <div class="stat-val" style="color:var(--review)">{rc}</div>
    <div class="stat-sub">human review</div></div>
  <div class="stat"><div class="stat-lbl">Fail</div>
    <div class="stat-val" style="color:var(--fail)">{fc}</div>
    <div class="stat-sub">gaps identified</div></div>
  <div class="stat"><div class="stat-lbl">Mean score</div>
    <div class="stat-val" style="color:var(--info)">{mean:.3f}</div>
    <div class="stat-sub">threshold: {thr} · coverage: {cov*100:.0f}%</div></div>
</div>"""

    # ── Score bars ─────────────────────────────────────────────────

    @classmethod
    def _render_score_bars(cls, validation_data: List[Dict],
                           threshold: float = 0.85) -> str:
        bars = []
        for row in validation_data:
            score = row.get('match_score', 0)
            color = ('var(--pass)' if score >= threshold
                     else ('var(--review)' if score >= 0.70
                           else 'var(--fail)'))
            label = f"§{row.get('section_id', '?')}"
            bars.append(
                f'<div class="s-row">'
                f'<div class="s-lbl">{label}</div>'
                f'<div class="s-bar"><div class="s-fill" '
                f'style="width:{score*100}%;background:{color}"></div></div>'
                f'<div class="s-val" style="color:{color}">{score:.2f}</div>'
                f'</div>')

        return f"""<div class="sec">
  <h3>Match scores by section</h3>
  <div class="legend">
    <span class="leg-i"><span class="leg-d" style="background:var(--pass)"></span>≥{threshold} pass</span>
    <span class="leg-i"><span class="leg-d" style="background:var(--review)"></span>0.70–{threshold} review</span>
    <span class="leg-i"><span class="leg-d" style="background:var(--fail)"></span>&lt;0.70 fail</span>
  </div>
  {''.join(bars)}
</div>"""

    # ── Validation table ───────────────────────────────────────────

    @classmethod
    def _render_validation_table(cls, validation_data: List[Dict]) -> str:
        rows = []
        for row in validation_data:
            status = row.get('status', '')
            badge_cls = ('b-pass' if status == 'PASS'
                         else ('b-mod' if status == 'REVIEW' else 'b-crit'))
            icon = '✓' if status == 'PASS' else ('⚠' if status == 'REVIEW' else '✗')
            rows.append(
                f'<tr>'
                f'<td>{html_escape(str(row.get("section_id","")))}</td>'
                f'<td>{html_escape(str(row.get("requirement_type","")))}</td>'
                f'<td>{html_escape(str(row.get("rag_retrieved","")))}</td>'
                f'<td>{row.get("match_score",0):.2f}</td>'
                f'<td><span class="badge {badge_cls}">{icon} {status}</span></td>'
                f'</tr>')

        return f"""<div class="sec">
  <h3>SQL verification results</h3>
  <table>
  <thead><tr><th>Section</th><th>Requirement</th><th>Retrieved</th>
    <th>Score</th><th>Status</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
  </table>
</div>"""

    # ── Gap analysis ───────────────────────────────────────────────

    @classmethod
    def _render_gap_analysis(cls, gaps: List[Dict]) -> str:
        cards = []
        for gap in gaps:
            sev = gap.get('severity', 'MODERATE').lower()
            badge_cls = 'b-crit' if sev == 'critical' else 'b-mod'
            card_cls = 'crit' if sev == 'critical' else 'mod'
            cards.append(
                f'<div class="gap {card_cls}">'
                f'<div class="gap-hdr">'
                f'<span class="badge {badge_cls}">'
                f'{html_escape(gap.get("severity",""))}</span>'
                f'<span style="font-size:11px;color:var(--fg3)">'
                f'§{html_escape(gap.get("section",""))}</span></div>'
                f'<div class="gap-t">'
                f'{html_escape(gap.get("title",""))}</div>'
                f'<div class="gap-d">'
                f'{html_escape(gap.get("description",""))}</div>'
                f'<div class="gap-r">→ '
                f'{html_escape(gap.get("recommendation",""))}</div>'
                f'</div>')

        return f"""<div class="sec">
  <h3>Gap analysis — LLM inference</h3>
  {''.join(cards)}
</div>"""

    # ── Cell rendering ─────────────────────────────────────────────

    @classmethod
    def _render_cell(cls, cell: Dict, include_code: bool = False) -> str:
        parts = []

        if cell['type'] == 'markdown':
            title = cell['source'].replace('#', '').strip().split('\n')[0]
            if title:
                parts.append(f'<div class="c-out"><div class="c-md">'
                             f'{html_escape(title)}</div></div>')

        elif cell['type'] == 'code':
            if include_code and cell['source'].strip():
                parts.append(f'<div class="c-code">'
                             f'{html_escape(cell["source"])}</div>')

            for output in cell.get('outputs', []):
                fmt = output.get('format', '')

                if fmt == 'html':
                    parts.append(f'<div class="c-out"><div class="c-html">'
                                 f'{output["content"]}</div></div>')

                elif fmt == 'text':
                    # Skip bridge markers and raw JSON in display
                    content = output['content']
                    content = re.sub(
                        r'__BRIDGE_\w+_(?:START|END)__', '', content).strip()
                    # Skip raw JSON blocks (already rendered in dashboard)
                    if content and not content.lstrip().startswith('{'):
                        parts.append(f'<div class="c-out"><div class="c-text">'
                                     f'{html_escape(content)}</div></div>')

                elif fmt == 'image_base64':
                    parts.append(
                        f'<div class="c-out"><img src="data:image/png;'
                        f'base64,{output["content"]}" '
                        f'style="max-width:100%;border-radius:6px"/></div>')

                elif fmt == 'error':
                    parts.append(f'<div class="c-out"><div class="c-err">'
                                 f'{html_escape(output["content"])}'
                                 f'</div></div>')

        return ''.join(parts)

    # ── Utility renderers ──────────────────────────────────────────

    @classmethod
    def _section_divider(cls, title: str) -> str:
        return f'<div class="sec-div">{html_escape(title)}</div>'

    @classmethod
    def _render_footer(cls, parsed: Dict) -> str:
        return f"""
<div class="pipe">
  <span>Notebook: {html_escape(parsed['filename'])}</span>
  <span>Kernel: {html_escape(parsed['kernel'])}</span>
  <span>Cells: {parsed['cell_count']} ({parsed['code_cells']} code)</span>
  <span>Rendered: {parsed['parsed_at'][:19]}</span>
</div>
<div class="ftr">
  <span>Notebook Output Bridge v3.0 — DTS/ESAVE</span>
  <span>Local-first · nbparameterise + nbclient + nbformat ·
    Zero HTTP ports</span>
</div>"""


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 5: Tier 2 — Jupyter-Native Display Functions
# ═══════════════════════════════════════════════════════════════════════════

def display_dashboard(notebook_path: str, include_code: bool = False):
    """
    Tier 2: Render a dashboard inside a Jupyter notebook cell.

    Call this from the harness notebook:
        from notebook_bridge import display_dashboard
        display_dashboard("executed.ipynb")

    The dashboard renders inline as rich HTML using IPython.display.
    No server process, no port binding.
    """
    from IPython.display import HTML, display

    html = DashboardRenderer.render_to_string(notebook_path, include_code)
    display(HTML(html))


def run_and_display(
    template_path: str,
    params: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = None,
    include_code: bool = False,
    kernel_name: str = 'python3',
    timeout: int = 600,
    also_save_html: Optional[str] = None
):
    """
    Tier 2: Execute a notebook with parameters AND render the dashboard
    inline — all in one call from a harness notebook.

    Usage from a Jupyter cell:
        from notebook_bridge import run_and_display

        run_and_display(
            "templates/esave_validation.ipynb",
            params={
                "spec_path": "/data/dpas_hce_ios_v1.0.pdf",
                "quality_threshold": 0.85,
                "model_endpoint": "http://localhost:11434"
            },
            also_save_html="output/report.html"
        )

    Args:
        template_path:  Path to the notebook template
        params:         Parameter overrides to inject
        output_path:    Where to save executed notebook
        include_code:   Show source code in dashboard
        kernel_name:    Jupyter kernel to use
        timeout:        Max seconds per cell
        also_save_html: Optional path to also save static HTML file (Tier 1)
    """
    from IPython.display import HTML, display as ipy_display

    # Execute
    executor = NotebookExecutor(
        kernel_name=kernel_name,
        timeout=timeout,
        on_cell_error='continue'
    )
    executed_path = executor.execute(template_path, output_path, params)

    # Render
    html = DashboardRenderer.render_to_string(executed_path, include_code)

    # Display inline (Tier 2)
    ipy_display(HTML(html))

    # Optionally also save to file (Tier 1)
    if also_save_html:
        DashboardRenderer.render_to_file(
            executed_path, also_save_html, include_code)
        _log(f"Also saved static HTML: {also_save_html}")

    return executed_path


def display_summary(notebook_path: str):
    """
    Display a compact summary of notebook results in a Jupyter cell.
    Lighter-weight than the full dashboard — just key metrics.
    """
    from IPython.display import HTML, display

    parsed = NotebookParser.parse(notebook_path)
    bridge = NotebookParser.extract_bridge_json(parsed)
    gaps = NotebookParser.extract_bridge_gaps(parsed)

    if not bridge:
        display(HTML(
            f'<div style="padding:12px;border-radius:8px;background:#FAEEDA;'
            f'border:1px solid #854F0B;font-size:13px;color:#633806">'
            f'No structured bridge data found in {html_escape(parsed["filename"])}. '
            f'Add __BRIDGE_JSON_START/END__ markers to enable the dashboard.</div>'))
        return

    total = bridge.get('total_sections', 0)
    pc = bridge.get('pass_count', 0)
    fc = bridge.get('fail_count', 0)
    rc = bridge.get('review_count', 0)
    mean = bridge.get('mean_score', 0)
    gap_count = len(gaps) if gaps else 0
    crit_count = sum(1 for g in (gaps or [])
                     if g.get('severity', '').upper() == 'CRITICAL')

    display(HTML(f"""
<div style="font-family:-apple-system,system-ui,sans-serif;padding:16px;
  border-radius:10px;background:#f1efe8;border:1px solid rgba(0,0,0,0.08)">
  <div style="font-size:12px;font-weight:600;color:#0F6E56;
    letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px">
    ESAVE Validation Summary — {html_escape(parsed['filename'])}</div>
  <div style="display:flex;gap:20px;flex-wrap:wrap;font-size:14px">
    <span><b>{total}</b> sections</span>
    <span style="color:#0F6E56"><b>{pc}</b> pass</span>
    <span style="color:#854F0B"><b>{rc}</b> review</span>
    <span style="color:#A32D2D"><b>{fc}</b> fail</span>
    <span style="color:#185FA5">mean: <b>{mean:.3f}</b></span>
    <span style="color:#A32D2D">{gap_count} gaps ({crit_count} critical)</span>
  </div>
</div>"""))


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 6: Batch Operations
# ═══════════════════════════════════════════════════════════════════════════

def batch_render(notebook_dir: str, output_dir: str,
                 include_code: bool = False) -> List[str]:
    """
    Render all notebooks in a directory to HTML dashboards.

    Useful for generating a batch of reports:
        python -c "from notebook_bridge import batch_render; \\
            batch_render('runs/', 'reports/')"
    """
    nb_dir = Path(notebook_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    notebooks = sorted(nb_dir.glob('*.ipynb'))
    results = []

    for nb_path in notebooks:
        out_path = out_dir / f"{nb_path.stem}.html"
        try:
            DashboardRenderer.render_to_file(
                str(nb_path), str(out_path), include_code)
            results.append(str(out_path))
        except Exception as e:
            _log(f"Error rendering {nb_path.name}: {e}")

    _log(f"Batch complete: {len(results)}/{len(notebooks)} rendered")
    return results


def generate_index(output_dir: str) -> str:
    """
    Generate an index.html that links to all rendered dashboards.
    Like the Flask index page, but as a static file.
    """
    out_dir = Path(output_dir)
    html_files = sorted(out_dir.glob('*.html'))
    html_files = [f for f in html_files if f.name != 'index.html']

    rows = []
    for f in html_files:
        size = f.stat().st_size
        size_str = f"{size // 1024}KB" if size > 1024 else f"{size}B"
        rows.append(
            f'<tr><td><a href="{f.name}" style="color:#185FA5;'
            f'text-decoration:none;font-weight:500">{f.name}</a></td>'
            f'<td style="color:#888780">{size_str}</td></tr>')

    index_html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESAVE Bridge — Reports</title>
<style>
:root{{--bg:#fafaf8;--fg:#2c2c2a;--fg3:#888780;--brd:rgba(0,0,0,0.08)}}
@media(prefers-color-scheme:dark){{:root{{
  --bg:#1a1a18;--fg:#e8e6dc;--fg3:#888780;--brd:rgba(255,255,255,0.06)}}}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);
  color:var(--fg);padding:36px;max-width:640px;margin:0 auto}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}
td{{padding:10px 14px;border-bottom:1px solid var(--brd)}}
</style></head><body>
<div style="font-size:11px;font-weight:600;letter-spacing:0.08em;
  text-transform:uppercase;color:#0F6E56;margin-bottom:4px">
  ESAVE — Notebook Output Bridge</div>
<h2 style="font-weight:500;font-size:18px">Validation reports</h2>
<p style="color:var(--fg3);font-size:12px;margin-top:5px">
  {len(html_files)} report(s) in {output_dir}</p>
<table>{''.join(rows)}</table>
<p style="margin-top:20px;font-size:10px;color:var(--fg3)">
  Notebook Output Bridge v3.0 · Local-first · No HTTP server</p>
</body></html>"""

    index_path = out_dir / 'index.html'
    with open(index_path, 'w') as f:
        f.write(index_html)

    _log(f"Index written: {index_path}")
    return str(index_path)


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 7: Utilities
# ═══════════════════════════════════════════════════════════════════════════

def _log(msg: str):
    """Simple logging with timestamp."""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[bridge {ts}] {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# MODULE 8: CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog='notebook_bridge',
        description='Notebook Output Bridge v3.0 — Local-first dashboard renderer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check environment
  python notebook_bridge.py check

  # Render existing notebook to HTML
  python notebook_bridge.py render -n executed.ipynb -o dashboard.html

  # Execute + render in one step
  python notebook_bridge.py run -n template.ipynb \\
      --params '{"spec_path": "/data/spec.pdf"}' -o report.html

  # Extract structured data
  python notebook_bridge.py extract -n executed.ipynb

  # Batch render all notebooks in a directory
  python notebook_bridge.py batch --dir runs/ --output reports/

  # Tier 2 (from inside Jupyter):
  #   from notebook_bridge import run_and_display
  #   run_and_display("template.ipynb", params={"spec_path": "..."})
""")

    sub = parser.add_subparsers(dest='command')

    # ── check ──
    sub.add_parser('check', help='Verify environment readiness')

    # ── render ──
    p = sub.add_parser('render', help='Render notebook to static HTML')
    p.add_argument('-n', '--notebook', required=True)
    p.add_argument('-o', '--output', default='dashboard.html')
    p.add_argument('--include-code', action='store_true')

    # ── run ──
    p = sub.add_parser('run', help='Execute notebook + render dashboard')
    p.add_argument('-n', '--notebook', required=True)
    p.add_argument('--params', help='JSON parameter overrides')
    p.add_argument('-o', '--output', default='dashboard.html',
                   help='HTML output path')
    p.add_argument('--executed', help='Path for executed .ipynb')
    p.add_argument('--kernel', default='python3')
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--include-code', action='store_true')

    # ── extract ──
    p = sub.add_parser('extract', help='Extract structured data as JSON')
    p.add_argument('-n', '--notebook', required=True)
    p.add_argument('--format', choices=['json', 'summary', 'text'],
                   default='summary')

    # ── batch ──
    p = sub.add_parser('batch', help='Render all notebooks in a directory')
    p.add_argument('--dir', required=True)
    p.add_argument('--output', default='reports/')
    p.add_argument('--include-code', action='store_true')
    p.add_argument('--index', action='store_true',
                   help='Also generate index.html')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == 'check':
        check_environment()

    elif args.command == 'render':
        path = DashboardRenderer.render_to_file(
            args.notebook, args.output, args.include_code)
        print(f"\nDone — open in browser:\n  {path}")

    elif args.command == 'run':
        params = json.loads(args.params) if args.params else None
        executor = NotebookExecutor(
            kernel_name=args.kernel, timeout=args.timeout)
        executed = executor.execute(
            args.notebook, args.executed, params)
        path = DashboardRenderer.render_to_file(
            executed, args.output, args.include_code)
        print(f"\nExecuted notebook: {executed}")
        print(f"Dashboard:         {path}")

    elif args.command == 'extract':
        parsed = NotebookParser.parse(args.notebook)
        if args.format == 'summary':
            print(json.dumps(
                NotebookParser.extract_summary(parsed), indent=2))
        elif args.format == 'json':
            bridge = NotebookParser.extract_bridge_json(parsed)
            gaps = NotebookParser.extract_bridge_gaps(parsed)
            print(json.dumps(
                {'bridge_data': bridge, 'gap_data': gaps}, indent=2))
        elif args.format == 'text':
            for t in NotebookParser.extract_all_text(parsed):
                print(t)
                print('---')

    elif args.command == 'batch':
        results = batch_render(args.dir, args.output, args.include_code)
        if args.index:
            generate_index(args.output)
        print(f"\nRendered {len(results)} dashboards to {args.output}")


if __name__ == '__main__':
    main()
