# Four-template meeting agent fusion

This is an independent visual prototype. It is not connected to meetingVAR or any live meeting assistant.

## Source-only composition

- `components/` and the chatbot styles come from `modern-ai-chatbot-interface-template.zip`.
- `sources/folders/` and its public portrait images come from `folders-ui.zip`.
- `sources/newsletter/` and `alt-placeholder.png` come from `newsletter-template.zip`. Its original remote background video is used with the original local fallback.
- `sources/login/` and `placeholder.svg` come from `two-column-login-card.zip`.

No external UI kit, icon set, font, illustration, color system or meetingVAR visual assets have been added. The original template brands, images, typography and motion are intentionally preserved as requested.

## Run

```sh
npm install
npm run dev
```

- `/` — original Newsletter composition with a local meeting-name form.
- `/workspace` — original AI chatbot shell, with the original Clips library under **Meeting clips**.
- `/login` — original two-column login composition. Submission only enters the demo.

All conversation and generation responses are static demonstrations. The prototype does not record meetings, call a model, verify evidence or create accounts. The original Redis newsletter action and analytics are not enabled.
