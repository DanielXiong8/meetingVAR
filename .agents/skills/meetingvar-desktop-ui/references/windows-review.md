# Windows review checklist

The product target is Windows; macOS is the development environment. A normal-browser preview can check layout and interactions but does not exercise pywebview, WebView2, native dialogs, Windows font fallback, or installer behavior.

Before calling a UI change ready for Windows, check it in the supported desktop runtime:

- Open the project-folder picker and read a folder containing Chinese names, spaces, nested directories, and a long path. Confirm the chosen Windows path is passed through unchanged.
- Check the default window size and a narrow window near 900×600. At 125% and 150% display scaling, confirm primary actions remain visible and controls do not overlap.
- Check keyboard focus, tooltips, dialogs, disabled controls, and error retry in WebView2. Confirm the page works without external font or CDN access.
- Check Windows high-contrast/forced-colors behavior and reduced-motion settings.
- Confirm the packaged UI matches the source changes. Rebuilding the installer is a separate Windows build step; editing web files does not update an installed executable.

For the synthetic browser preview available on the 0.1.2 refresh branch, run `python3 desktop/preview_ui.py --port 8765` from the repository root and open `http://127.0.0.1:8765`. The preview is intentionally synthetic: it cannot validate a real engine, model, credential, device, or Windows native control.

When reporting verification, state the platform, runtime, and scenarios actually exercised. Do not describe browser checks as Windows acceptance.
