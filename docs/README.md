# Documentation

| Guide | For |
| --- | --- |
| [user-guide.md](user-guide.md) | Everyone. Projects, analyses, diagrams, scorecard, history, fixes. |
| [ai-configuration.md](ai-configuration.md) | Connecting an AI provider - and what still works without one. |
| [guided-fixes.md](guided-fixes.md) | The fix catalogue and the safety rules around applying changes. |
| [architecture.md](architecture.md) | Contributors. How the codebase fits together. |
| [troubleshooting.md](troubleshooting.md) | When something does not work. |
| [shortcuts.md](shortcuts.md) | Keyboard reference. |
| [testing.md](testing.md) | How to run the suites, including the in-browser JS tests. |

The project [README](../README.md) covers the two interfaces, installation and
first run. In short:

```powershell
.\.venv\Scripts\python -m app        # the WebView2 interface
.\.venv\Scripts\python -m app.ui     # the native interface, no browser engine
```

Both share every line of analysis, scoring, diagram and export code; only the
window differs.
