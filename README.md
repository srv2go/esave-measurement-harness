# Notebook Output Bridge v3.0 — Local-First Edition

**ESAVE Contribution to attempt workaround for extracting results for POC Dashboard offline version**

A lightweight library that executes, parses, and renders Jupyter notebook
outputs as rich HTML dashboards — entirely local, no server process required.

```
Zero HTTP ports. Zero new servers. Zero governance friction.
```

---

## What this solves

Your RAG validation pipeline (Docling → mxbai-embed-large → Llama 8B →
SQL verification) runs in Jupyter notebooks. The results are trapped inside
the notebook UI. Gradio and Streamlit are blocked by the AI council. This
bridge extracts notebook outputs and renders them as clean dashboards —
either as static HTML files or inline inside JupyterHub.


## What's in the package

```
notebook_bridge/
├── notebook_bridge.py                  ← The bridge (single file, no server)
├── harness_notebook.ipynb              ← Tier 2 runner — open in JupyterHub
├── notebooks/
│   └── rag_validation_pipeline.ipynb   ← Sample validation notebook
├── output/                             ← Generated dashboards land here
│   ├── dashboard.html
│   └── index.html
└── README.md                           ← This file
```


## Dependencies

Everything ships with Jupyter except nbparameterise (one pip install):

| Package          | Ships with Jupyter? | Purpose                       |
|-----------------|---------------------|-------------------------------|
| nbformat        | Yes                 | Read/write .ipynb files       |
| nbclient        | Yes (6.5+)          | Execute notebooks via kernel  |
| nbparameterise  | **No — install it** | Parameter injection           |
| nbconvert       | Yes                 | Optional fallback HTML export |
| IPython         | Yes (in Jupyter)    | Tier 2 inline display         |

```bash
pip install nbparameterise
```

That is the only install. Everything else is already in your environment.


---

## Quick start

### 1. Check your environment

```bash
python notebook_bridge.py check
```

Expected output:
```
Required packages:
  ✓ nbformat (5.10.4)
  ✓ nbclient (0.10.4)
  ✓ nbparameterise (0.6.1)

Tier 1 (static file render):   READY
Tier 2 (Jupyter-native):       READY (when run inside Jupyter)
Tier 3 (Flask server):          NOT INCLUDED — governance restricted
```


### 2. Tier 1 — Generate a static dashboard (no server)

```bash
python notebook_bridge.py render \
    -n notebooks/rag_validation_pipeline.ipynb \
    -o output/dashboard.html
```

Open `output/dashboard.html` in any browser. Done.

To share: copy the HTML file to a shared drive, email it, or drop it in
a SharePoint/Confluence page. The file is self-contained — all CSS is inline,
no external dependencies, no JavaScript, dark mode built in.


### 3. Tier 2 — View dashboard inside JupyterHub

Open `harness_notebook.ipynb` in JupyterHub. Edit the parameters in
Step 2. Run all cells. The dashboard renders inline in the notebook.

Or from any notebook cell:
```python
from notebook_bridge import run_and_display

run_and_display(
    "notebooks/rag_validation_pipeline.ipynb",
    params={
        "spec_path": "/data/dpas_hce_ios_v1.0.pdf",
        "quality_threshold": 0.85,
    },
    also_save_html="output/report.html"  # optional Tier 1 copy
)
```


---

## Delivery modes explained

### Tier 1: Static file render

```
Template.ipynb → nbparameterise → nbclient → executed.ipynb
                                                    ↓
                              notebook_bridge renderer
                                                    ↓
                                          dashboard.html
                                                    ↓
                                    Shared drive / email / browser
```

- **Zero server process** — output is a file on disk
- **No HTTP port** — nothing to govern
- **Self-contained HTML** — inline CSS, dark mode, responsive
- **Same class as nbconvert** — if nbconvert is approved, this is too


### Tier 2: Jupyter-native display

```
harness_notebook.ipynb (in JupyterHub)
        ↓
  from notebook_bridge import run_and_display
        ↓
  nbparameterise → nbclient → executed.ipynb
        ↓
  DashboardRenderer.render_to_string()
        ↓
  IPython.display.HTML(html_string)
        ↓
  Dashboard renders inline in notebook cell
```

- **No new server** — uses existing JupyterHub (already approved)
- **IPython.display is core Jupyter** — no additional packages
- **Shareable via JupyterHub URL** — or export the harness notebook to HTML
- **The harness notebook IS the audit trail** — parameters, execution, results in one file


---

## How to adapt your existing notebooks

Your existing notebooks work with the bridge as-is — it renders all cell
outputs in order. To enable the rich dashboard view (stats cards, score
bars, gap analysis cards), add three things:

### 1. Tag your parameters cell

In your notebook, find the cell that defines default parameters. Add
`"parameters"` to its cell metadata tags:

```
Cell metadata → tags → ["parameters"]
```

In JupyterHub: click the cell → View → Cell Toolbar → Tags → type
"parameters" → press Add Tag.

This tells nbparameterise which cell to override. Your cell might look like:

```python
# Parameters
spec_path = 'default_spec.pdf'
embedding_model = 'mixedbread-ai/mxbai-embed-large-v1'
quality_threshold = 0.85
chunk_size = 1024
```


### 2. Add bridge JSON markers to your summary output

Wherever your notebook prints summary statistics, wrap the structured
data in markers:

```python
import json

summary = {
    'total_sections': 12,
    'pass_count': 8,
    'review_count': 2,
    'fail_count': 2,
    'mean_score': 0.834,
    'coverage': 0.833,
    'threshold': 0.85,
    'validation_data': [
        {
            'section_id': '4.2.1',
            'requirement_type': 'Transaction Flow',
            'normative': 'Yes',
            'rag_retrieved': 'Yes',
            'match_score': 0.94,
            'status': 'PASS'    # PASS, REVIEW, or FAIL
        },
        # ... more rows
    ]
}

# Human-readable output (still works in raw notebook view)
print(f"Pass: {summary['pass_count']}/{summary['total_sections']}")

# Machine-readable for the bridge
print('__BRIDGE_JSON_START__')
print(json.dumps(summary, indent=2))
print('__BRIDGE_JSON_END__')
```


### 3. Add gap analysis markers (optional)

If your notebook produces gap analysis from LLM inference:

```python
gaps = [
    {
        'severity': 'CRITICAL',     # CRITICAL or MODERATE
        'section': '6.1.2',
        'title': 'ODA Parameters Missing',
        'description': 'Offline Data Authentication parameters not retrievable.',
        'recommendation': 'Add AIP/AFL configuration table per EMV Book 3 §6.5.'
    },
    # ... more gaps
]

print('__BRIDGE_GAPS_START__')
print(json.dumps(gaps, indent=2))
print('__BRIDGE_GAPS_END__')
```

These are just print statements. No imports, no dependencies, no code
changes to your pipeline logic. The markers are invisible in normal
notebook usage — they only activate when the bridge parses the outputs.


---

## CLI reference

```
notebook_bridge.py check
    Verify environment readiness — lists installed packages and tier status.

notebook_bridge.py render -n NOTEBOOK -o OUTPUT [--include-code]
    Render an existing executed notebook to static HTML.
    -n, --notebook    Path to .ipynb file (required)
    -o, --output      HTML output path (default: dashboard.html)
    --include-code    Include source code cells in output

notebook_bridge.py run -n NOTEBOOK [--params JSON] -o OUTPUT [options]
    Execute a notebook with parameters, then render to HTML.
    -n, --notebook    Path to template .ipynb (required)
    --params          JSON string of parameter overrides
    -o, --output      HTML output path (default: dashboard.html)
    --executed        Path for the executed .ipynb (default: <stem>_executed.ipynb)
    --kernel          Jupyter kernel name (default: python3)
    --timeout         Seconds per cell (default: 600)
    --include-code    Include source code in output

notebook_bridge.py extract -n NOTEBOOK [--format json|summary|text]
    Extract structured data from notebook outputs.
    -n, --notebook    Path to .ipynb file (required)
    --format          Output format (default: summary)
                      summary = bridge data + gaps + metadata
                      json = raw bridge/gap data only
                      text = all text outputs

notebook_bridge.py batch --dir DIR --output DIR [--index] [--include-code]
    Render all notebooks in a directory to HTML dashboards.
    --dir             Input directory containing .ipynb files
    --output          Output directory for HTML files
    --index           Also generate an index.html linking all reports
    --include-code    Include source code in output
```


## Python API reference

For use from other notebooks or scripts:

```python
from notebook_bridge import (
    NotebookExecutor,       # Execute notebooks with parameters
    NotebookParser,         # Parse .ipynb and extract structured data
    DashboardRenderer,      # Render HTML dashboards
    run_and_display,        # Tier 2: execute + display inline
    display_dashboard,      # Tier 2: display existing notebook
    display_summary,        # Tier 2: compact summary bar
    batch_render,           # Render all notebooks in a directory
    generate_index,         # Create index.html for a reports directory
    check_environment,      # Verify dependencies
)

# ── Execute with parameters ──────────────────────────────────
executor = NotebookExecutor(kernel_name='python3', timeout=600)
executed = executor.execute(
    'template.ipynb',
    output_path='runs/executed.ipynb',
    parameters={'spec_path': '/data/spec.pdf', 'quality_threshold': 0.90}
)

# ── Parse and extract ────────────────────────────────────────
parsed = NotebookParser.parse('runs/executed.ipynb')
bridge_data = NotebookParser.extract_bridge_json(parsed)
gaps = NotebookParser.extract_bridge_gaps(parsed)
summary = NotebookParser.extract_summary(parsed)

# ── Tier 1: Static file ─────────────────────────────────────
DashboardRenderer.render_to_file('runs/executed.ipynb', 'output/report.html')

# ── Tier 2: Jupyter inline (call from a notebook cell) ──────
from notebook_bridge import run_and_display
run_and_display('template.ipynb', params={...})

# or for an existing executed notebook:
from notebook_bridge import display_dashboard
display_dashboard('runs/executed.ipynb')

# ── Batch operations ─────────────────────────────────────────
results = batch_render('runs/', 'reports/')
generate_index('reports/')
```


---

## Connecting to ESAVE

The bridge's parser makes it straightforward to feed results back into
ESAVE's multi-agent pipeline:

```python
from notebook_bridge import NotebookExecutor, NotebookParser

# 1. Execute the D&A team's RAG validation notebook
executor = NotebookExecutor()
executed = executor.execute(
    'dna_rag_validation.ipynb',
    parameters={'spec_path': '/data/dpas_hce_ios_v1.0.pdf'}
)

# 2. Extract structured results
parsed = NotebookParser.parse(executed)
bridge_data = NotebookParser.extract_bridge_json(parsed)
gaps = NotebookParser.extract_bridge_gaps(parsed)

# 3. Feed failures into ESAVE agents for deeper analysis
failures = [s for s in bridge_data['validation_data'] if s['status'] == 'FAIL']
for section in failures:
    # Hand off to ESAVE Compliance Validator agent
    esave_compliance_agent.analyze(
        section_id=section['section_id'],
        requirement_type=section['requirement_type'],
        retrieval_score=section['match_score']
    )
```


---

## Troubleshooting

**"ModuleNotFoundError: No module named 'nbparameterise'"**
```bash
pip install nbparameterise
```

**"No module named 'nbclient'"**
Your Jupyter installation may be older than 6.5. Update:
```bash
pip install --upgrade nbclient
```

**"No kernel found" or "kernel not available"**
Check your kernel name:
```bash
jupyter kernelspec list
```
Then pass the correct name: `NotebookExecutor(kernel_name='your_kernel')`

**"Execution timeout"**
Default is 600s (10 min). For long-running pipelines:
```python
executor = NotebookExecutor(timeout=1200)  # 20 min
```

**"No structured bridge data found"**
Your notebook doesn't have `__BRIDGE_JSON_START/END__` markers. The bridge
still renders all cell outputs — the markers just enable the rich dashboard
(stats cards, score bars, gap cards). See "How to adapt your existing
notebooks" above.

**Cell execution fails mid-notebook**
By default, `on_cell_error='continue'` — the bridge captures partial
results and continues through remaining cells. The terminal emit cell
(which writes result/trace/metrics JSON) should still run. Error cells
are highlighted in the dashboard with a red background.

**"IPython not found" when using display_dashboard()**
This function only works inside Jupyter. For command-line usage, use
Tier 1 instead:
```bash
python notebook_bridge.py render -n executed.ipynb -o dashboard.html
```


---

## Architecture notes for the D&A team

This bridge is intentionally thin. The execution engine is nbclient
(which you already have). The parameter injection is nbparameterise
(one small package). The renderer is pure Python string formatting —
no template engine, no JavaScript framework, no build step.

The entire bridge is a single file: `notebook_bridge.py`. Drop it next
to your notebooks and import it. No package installation beyond
nbparameterise.

We deliberately excluded Flask/Gradio/Streamlit to avoid the AI council's
HTTP port restriction. If that restriction is ever lifted, a server layer
can be added on top of the same renderer without changing any of the
parsing or execution logic.

The bridge does not modify your notebooks. It reads the executed .ipynb
JSON, extracts outputs, and renders HTML. Your pipeline, your models,
your data — untouched.
