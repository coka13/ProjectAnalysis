# Guided fixes

The **Guided fixes** view lists mechanical defects found by static analysis and,
where the repair is unambiguous, offers a unified diff for it.

## The safety contract

1. **Nothing is written until you confirm.** Generating proposals and previewing
   diffs are read-only operations. The backend rejects an apply request that does
   not carry an explicit `confirm` flag, so a bug in the UI cannot cause a write.
2. **Proposals are pinned to file contents.** Every proposal carries a SHA-256
   digest of the file it was computed from. If the file changed after the scan -
   because you edited it, switched branch or pulled - that file is skipped and
   reported as a failure rather than overwritten.
3. **Only local projects.** A project backed by a remote git URL is analysed in a
   throwaway clone, so fixes there would be silently discarded. The backend
   refuses to apply fixes to anything but a local folder.
4. **No path escapes.** Target paths are resolved under the project root and any
   path that lands outside it is rejected.
5. **No model in the loop.** Every rule is a pure text transformation, so the
   diff you review is exactly the diff that is applied. Rules that need human
   judgement are still reported, but produce no patch.

Commit or stash your work before applying anything. The app does not create a
backup - your version control is the undo button.

## Rule catalogue

There are 32 rules. Twelve carry a transform and can be applied for you; the
other twenty are advisory and only report. The twelve auto-fixable ones are
listed in full below, followed by two representative advisory rules.

| Rule | Language | Severity | Auto-fixable | What it does |
| --- | --- | --- | --- | --- |
| `bare-except` | Python | high | yes | `except:` → `except Exception:` |
| `none-identity` | Python | low | yes | `== None` → `is None`, `!= None` → `is not None` |
| `yaml-unsafe-load` | Python | critical | yes | `yaml.load(` → `yaml.safe_load(` |
| `py2-except-syntax` | Python | critical | yes | `except X, e:` → `except X as e:` |
| `deprecated-unittest-alias` | Python | high | yes | `assertEquals` → `assertEqual`, `failUnless` → `assertTrue`, and ten more |
| `invalid-escape-sequence` | Python | medium | yes | promotes a literal to a raw string, e.g. `"\d+"` → `r"\d+"` |
| `debugger-statement` | JS/TS | medium | yes | deletes a stray `debugger;` line |
| `typeof-loose-equality` | JS/TS | low | yes | `typeof x == 'string'` → `typeof x === 'string'` |
| `js-wrapper-constructor` | JS/TS | low | yes | `new Array()` → `[]`, `new Object()` → `{}` |
| `trailing-whitespace` | any | low | yes | strips trailing spaces and tabs |
| `missing-final-newline` | any | low | yes | appends a terminating newline |
| `trailing-blank-lines` | any | low | yes | collapses blank lines at end of file to one newline |
| `subprocess-shell` | Python | critical | **no** | reports `shell=True` |
| `innerhtml-sink` | JS/TS | high | **no** | reports assignment to `innerHTML` |

Advisory rules deliberately produce no patch. Removing `shell=True` or replacing
an `innerHTML` assignment changes behaviour, and a wrong "fix" is worse than no
fix.

Every auto-fixable rule is either a construct the language itself has already
removed, or a rewrite that provably cannot change behaviour:

- `py2-except-syntax` and `deprecated-unittest-alias` target syntax and names
  that no longer exist in current Python, so the old form cannot be the intended
  one.
- `typeof-loose-equality` is safe because `typeof` always yields a string and it
  is compared against a string literal, so `==` and `===` cannot disagree.
- `invalid-escape-sequence` only promotes a literal to raw when *every*
  backslash escape inside it is invalid. A literal that mixes a real escape with
  an invalid one, such as `"line\nbreak \d"`, is left alone - making it raw would
  silently change the value. A test compiles the literal before and after and
  asserts the two strings are equal.

Each proposal reports the problem, the root cause, the impact, an effort estimate
and a confidence value, so you can triage without opening the file.

## Line endings and encoding

Files are read and written verbatim (`newline=""`), so a CRLF file stays CRLF.
Files that are not valid UTF-8 are skipped rather than lossily re-encoded.

## Adding a rule

Rules live in `app/ai/fixes.py` as `Rule` instances in the `RULES` tuple. An
auto-fixable rule supplies `transform(text) -> (new_text, touched_lines)`; an
advisory rule supplies `detect(text) -> touched_lines`. A transform must be
idempotent - `tests/test_fixes.py` asserts that applying the catalogue twice is a
no-op.
