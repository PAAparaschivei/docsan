# Synthetic example

**Try one Word document containing invented company and person names, with no private input.**

The example profile and input generator use the fictional names "Aurelan Group" and "Elena Marchetti". They are test data, not a real company dictionary. Generated Word files and reports are deliberately absent from the repository.

From the repository root:

```bash
cd engine
uv sync --locked
uv run python ../examples/make_demo.py ../build/demo
uv run docsan run ../build/demo/input.docx --profile ../examples/profile.json --lexicon tests/fixtures/lexicon --private ../build/demo/private --out ../build/demo/export
uv run python ../examples/check_demo.py ../build/demo/export
```

The input says:

```text
Aurelan Group runs the integration platform.
Elena Marchetti approved the plan.
```

The output should remove the planted company and surname while preserving the integration/platform context. The separate example check inspects the Word package, including metadata, rather than accepting an exit code as proof that an export exists.

Open the input and output in Word or LibreOffice to compare them. This small example demonstrates the workflow, not broad document coverage. The generator refuses to replace an existing input; use a new directory to repeat it.

These commands keep the demo's processing state under `build/demo/private`, separate from normal Application Support storage. The native app has its own sandboxed storage. Never replace the example profile with a real company profile in a public checkout.
