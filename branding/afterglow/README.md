# AFTERGLOW

A private picture house. Stay for one more.

An independent cinema identity for this Jellyfin instance: a setting sun broken into film-like horizontal bands, spacious ivory lettering, and restrained ember light against ink navy.

## Assets

- `brand-sheet.png`: identity concept with emblem, wordmark, app tile, and palette.
- `cinematic-background.png`: standalone text-free background with dark negative space for login controls.

These are raster artwork assets; the logo applications in the brand sheet are concept presentations, not separately exported transparent logos. No live Jellyfin configuration was changed.

## Palette

| Colour | Hex |
| --- | --- |
| Ink | `#101722` |
| Ivory | `#F3EBDD` |
| Ember | `#FF784F` |
| Dusty lilac | `#A99BCB` |

## Apply in Jellyfin

1. Open **Dashboard > Branding**. Save any existing CSS and splash image before replacing them.
2. Upload `cinematic-background.png` as the custom splash screen image and leave the splash screen enabled. The brand sheet is a presentation, not the splash asset.
3. Paste the contents of `custom.css` into **Custom CSS**.
4. Optionally set the server name in General settings to `AFTERGLOW`. The CSS already displays the wordmark and tagline on the login page; leave the login disclaimer for any actual server notice.
5. Save, select the **Dark** display theme, and refresh the web client.

The stylesheet uses Jellyfin's existing login backdrop, with no hardcoded server URL or external dependencies. It keeps the existing login controls and text. CSS affects clients using Jellyfin Web; other clients decide how to use the splash image. The initial loading logo and native app icons are separate from the login splash artwork.

Replace the previous CSS entirely rather than appending this revision. The login-only overlay override uses `:has()` and needs a modern web client. Browsers without support keep the darker default backdrop. The default header logo is replaced with a decorative text wordmark; CSS does not change the server's accessible name, so use the server name setting as well.

Validated on the actual Tailscale login page using Chromium at 1440×1000 and 390×844, with browser-local replacement of the branding response. The sunset, wordmark, manual login controls, checkbox, and secondary buttons rendered correctly, with no horizontal overflow on mobile. See `preview-desktop.png` and `preview-mobile.png`. Saved server settings were not changed. Authenticated library/playback views and other clients were not tested. After applying, check user selection, keyboard navigation, library browsing, and playback. To roll back, restore the previous CSS and splash image in Branding.

References: [CSS customization](https://jellyfin.org/docs/general/clients/css-customization/), [splash screen settings](https://jellyfin.org/docs/general/server/settings/).

## Library styling

The complete `custom.css` also includes library styling: ivory headings and titles, muted metadata, rounded artwork, ember focus/hover outlines and playback progress, a subtle warm home-page background, and matching default placeholder tiles. Poster dimensions, responsive row behaviour, images, and card actions use Jellyfin's existing implementation. Uploaded or server-generated tile images retain their colours; CSS only recolours actual default placeholders.

Library selectors were checked against Jellyfin Web v10.11.11 `home.html`, `card.scss`, `cardBuilder.js`, and `indicators.scss`. Representative library card markup was checked in Chromium with the running server's styles at desktop and mobile widths, including image preservation, long-title wrapping, and horizontal overflow. This is component-level validation, not a signed-in library or playback check. The login-only backdrop rules remain separate from library styling.

## Image generation prompts

Generated using the built-in image generation tool.

### Brand sheet prompt

Use case: logo-brand
Create a beautifully art-directed brand identity presentation for a personal Jellyfin media server called AFTERGLOW. User requests creative branding; this is our original chosen identity: an intimate late-night private cinema.
Landscape 1536x1024 brand sheet, impeccably composed Swiss editorial grid with generous negative space, high-end independent cinema identity. Deep ink blue #101722 background, ivory #F3EBDD typography, ember #FF784F and muted lilac #A99BCB accent.
Upper two-thirds: dominant bespoke AFTERGLOW uppercase wordmark, elegant confident wide geometric sans with carefully spaced letters. An original simple emblem of a setting sun: ivory upper semicircle hovering above three horizontal ember lines, the middle line slightly offset to subtly suggest motion and film frames. No triangle play icon. Small tasteful text "YOUR PRIVATE PICTURE HOUSE" above; tagline "Stay for one more." below. Right side dramatic restrained abstract orange sunset halo with fine analog film grain fading into near black, no purple neon sci-fi.
Lower third separated by fine hairline: three clear identity applications: square app tile with sun emblem, compact horizontal emblem + AFTERGLOW lockup, four elegant color swatches with hex labels "#101722" "#F3EBDD" "#FF784F" "#A99BCB". Small footer text "AFTERGLOW / PERSONAL CINEMA".
Everything feels like a real designer portfolio identity sheet, flat finished artwork, not a photograph of stationery. Text must be exact and legible. No copyrighted movie artwork, no fake UI, no extraneous copy.

### Background prompt

undefined
