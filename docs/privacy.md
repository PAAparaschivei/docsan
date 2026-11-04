# Privacy boundary

**DocSan's document processing runs locally. A cloud-connected agent observing it can still receive confidential content.**

## What stays on the Mac

The native app copies selected files into its sandbox container and runs its bundled Python engine through local process pipes. Apple Vision reads images and PDF page renderings on the device. Tesseract provides a separate local OCR pass. Matching, review, output rebuilding and verification use local code, dictionaries and the private profile. No cloud OCR, LLM endpoint, provider login, API key or telemetry service is part of this processing path.

The app's own page renderings and OCR images stay local. A screenshot inside an input document is processed like another picture; it is not sent to an AI provider by DocSan.

The source-build setup downloads Python, packages, OCR data and build dependencies. That happens before confidential inputs are needed. Once the app is built, processing works without those download services.

## Enforcement and evidence

| Evidence | Boundary |
|---|---|
| [App entitlements](../app/DocSan/DocSan.entitlements) and [build definition](../app/project.yml) | App Sandbox enabled with user-selected file access; no network client/server entitlement |
| [Helper entitlements](../app/scripts/engine-helper.entitlements) | Bundled engine/helpers inherit the sandbox |
| [Engine client](../app/DocSan/Engine/EngineClient.swift) | Local subprocess and JSON pipes; isolated Python invocation and a restricted environment |
| [Storage implementation](../app/DocSan/Support/Storage.swift) | Private container for originals, profiles, staging and reports; hash-checked export to a user-selected folder |
| [Architecture](architecture.md) and [tests](../engine/tests/test_bridge.py) | Documented processing boundaries and synthetic workflow checks |

You can inspect a built app's signature and entitlements locally:

```bash
codesign --verify --deep --strict /PATH/TO/DocSan.app
codesign -d --entitlements :- /PATH/TO/DocSan.app
codesign -d --entitlements :- /PATH/TO/DocSan.app/Contents/Resources/engine/python/bin/python3
```

Replace the paths with your evaluation build. The Python helper should show sandbox inheritance and the app should show no network entitlements. Source checks and synthetic CI do not prove the behavior of a differently modified or signed binary, or audit the rest of your Mac.

The standalone CLI uses the same local engine but does not acquire the app's sandbox merely by running `uv run docsan`. It has the permissions of the invoking process. External monitoring, cloud sync, backups and screen-recording software also have their own access and policies.

## Screenshots and cloud-connected agents

If a cloud-connected assistant receives a screenshot showing the original document, its preview, filenames, private profile or logs, the visible information has left the local workflow. The same applies when a tool returns extracted text, an accessibility tree, clipboard contents or file data. Sanitizing the document later cannot retract information already sent.

This is distinct from model training. A provider's no-training setting or enterprise agreement can change its data-handling terms; it does not make remote processing local. OpenAI's [computer-use documentation](https://developers.openai.com/api/docs/guides/tools-computer-use) explicitly describes returning screenshots/page text to the model. Its [screen-access guidance](https://learn.chatgpt.com/docs/computer-use) treats visible app content as context ChatGPT may process. Check the corresponding policy for any other assistant.

For a workflow requiring confidential material to remain local:

1. Use DocSan directly. Stop cloud-connected agents, screen sharing and capture tools that can observe the app while originals or profiles are visible. Merely minimizing an agent window does not revoke its access.
2. Keep originals, private profiles, working state and exports in approved local folders outside cloud sync. DocSan cannot prevent another application from uploading files it can read.
3. Review text, pictures, PDF masks and indirect identifying context locally before export and before authorized sharing. A verified result is not universal anonymization.
4. Reproduce support issues with invented inputs. Never paste real profiles, originals, screenshots or diagnostic logs into a public issue or agent conversation.

The README screenshot uses a separate demonstration app container and invented content. No real document or production profile is needed to reproduce it.
