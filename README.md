# Research Final Report Generator

A Windows desktop app that guides Senior High School research groups through a system development research paper and produces the finished report in their school's Word format. Students answer in Tagalog, Taglish, or English. The app drafts in formal academic English, and a person must edit and approve every part.

Reports stay on the student's computer in one saved file. No account is needed, and internet is needed only for the AI and source search.

For the full design, build status, and commands, read [project context.md](project%20context.md).

## Develop

```
.venv\Scripts\python.exe -m pytest -q tests
node --test relay/test/relay.test.mjs
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`packaging\build.ps1` makes the app folder and the portable ZIP. `packaging\build_installer.ps1` makes the installer and needs Inno Setup 6.

The AI runs through the small Cloudflare Worker in `relay/`. The app contains no AI key.
