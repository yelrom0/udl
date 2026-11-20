# The orch screens (29.1)

Reference screens for Phase 29's hand-built pages (29.3–29.9), drawn in Google Stitch. Each UI task builds
its screen in React and daisyUI against these pictures and never pastes Stitch's HTML (O-C10). The
design system they follow, with both themes' tokens, the shell and udl's own vocabulary (task states,
agent status, link chips), is [DESIGN.md](DESIGN.md).

- **Stitch project:** [udl orchestrator](https://stitch.withgoogle.com/projects/15107434053989671605)
  (private to the owner's Google account), with the design systems "udl daisyUI light" and "udl daisyUI dark".
- **Files:** `<screen>-<theme>-<size>.png`. Desktop is 1440 CSS px wide at 1×; phone is 390 CSS px wide at 2×.

## How they were made

Stitch drew each screen's layout and content from a prompt, but it colours a screen with a palette of
its own (Material roles such as `surface-container-low`, with its own primary and error), so its
pictures are not daisyUI's. Each screen's HTML was therefore re-themed onto daisyUI 5.8.1's own `light`
and `dark` themes: every colour it uses (Stitch's roles, Tailwind's palettes, hex values) became an
expression over daisyUI's CSS variables (`--color-base-100`, `--color-primary`…), and the page was
rendered in Chromium with `data-theme="light"` and `data-theme="dark"`. **A dark screen is its light
screen's layout with daisyUI's dark tokens, nothing else.** Those renders, and the re-themed HTML,
replaced Stitch's own, so the Stitch project shows the same pictures as this folder.

## Reading them

- Colours, spacing, hierarchy, what each screen holds and where: follow the picture.
- Component shapes are close, not exact: Stitch drew some screens on daisyUI 4's CSS and others in plain
  Tailwind. Build with daisyUI 5's own components (`btn`, `badge`, `card`, `tabs`, `menu`…) in the
  shell's theme, and the result will match.
- The shell around each page (sidebar, navbar) is drawn only so the page sits in it: the shell is
  18.5's and is not rebuilt from these.
- The data is udl's own plan, with made-up details where a screen needs them (PR numbers, costs, the
  questions' text).

## Screens

| Screen | Task | Desktop (1440 px) | Phone (390 px) |
|---|---|---|---|
| Inbox | 29.3 | [light](inbox-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=4fbab1751017480cacc6609079604f69)) · [dark](inbox-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=fddee541cf074971aec7566bd70cf58a)) | [light](inbox-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=6c577ec04aa5438a903a91626e61388f)) · [dark](inbox-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=ebfcc3fa8f2d426a99640ee1e0a0c652)) |
| Board | 29.4 | [light](board-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=a01ede4ba0104abf9ad5eb2765efda0d)) · [dark](board-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7051af8570a747ab841bc745f8ec20c2)) | [light](board-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=714267e7946f417488a3f789e8f42688)) · [dark](board-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=35c29045a2ba4d27844663b7aa1044bd)) |
| Feature | 29.5 | [light](feature-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d2657d70cd2d41f29da24fb19918b6cf)) · [dark](feature-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=4dc4588af1d843e8820efd62c771d5cc)) | [light](feature-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=1d4c2ed96c2a4aa189e87ef8718fccf9)) · [dark](feature-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=51abd9cf242f4b79abe75aa577dddcd2)) |
| Task | 29.5 | [light](task-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=6423d930355047cb84df2a85eaaca868)) · [dark](task-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=bd90d38f4a3b449495a9f43bff0fa93b)) | [light](task-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=22c404ee1d564a5a8e72da8d588f5cd4)) · [dark](task-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=b4dd135234bd406fab8a0321554992a3)) |
| Agents (the hierarchy) | 29.6 | [light](agents-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7791ff8dfd96441a8f407efbda0f23b5)) · [dark](agents-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d17fed4e665c45a880b2114968ccdcb3)) | [light](agents-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=ddefdd0cfbfb4718a0a6d7650b5754f1)) · [dark](agents-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0e287012078b43938fff6f0b4b492fa8)) |
| Agent (queue and live session) | 29.6 | [light](agent-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=da1624bace6c4b98a0d64c45d05165a0)) · [dark](agent-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=fa261cca352745de8be29520980348ed)) | [light](agent-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=e4a4d6e0423e4ce989f0222376cac769)) · [dark](agent-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=27ecbc8414ca40a091f73a3dac131dc2)) |
| Q&A (list, and a thread on desktop) | 29.7 | [light](qa-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=47ac5610e253455c9b7f4ad2b27f6828)) · [dark](qa-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=bcb6abba194a4d818f16dbbf0488fcf2)) | [light](qa-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=efa3ed1403154b6faf6f655196af030b)) · [dark](qa-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=cb6281e6a610495a94e0ac79aabf46cf)) |
| Q&A thread (phone) | 29.7 | — | [light](qa-thread-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=39735072e25c4bf08794d011655189f8)) · [dark](qa-thread-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=046e8294e25c40b9b1c3ff330ec361db)) |
| Ready to merge | 29.8 | [light](merge-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=dad3482589b143abb7c0266c6562e6bc)) · [dark](merge-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=2584378a5ef04df0a0e8f045046b3b6b)) | [light](merge-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=529dd7d5434a491c82404792620125bd)) · [dark](merge-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=48bf3cac921645f28f8a45e23e97fc56)) |
| To action | 29.8 | [light](actions-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0c0b84dc96674e279f156dce50032e96)) · [dark](actions-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0fd28d661b034e41b6abd24c82305a09)) | [light](actions-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=f55c20038eff4331a1ea94f4b4254a54)) · [dark](actions-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d79a7ae6f71940d79d37fa5b8244a3d1)) |
| Connections: a generated page (reference only) | 29.9 | [light](connections-light-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=126785af99b04553983316310fb2fd68)) · [dark](connections-dark-desktop.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=b4193d88b702431088fb00a576accdd4)) | [light](connections-light-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7272b669d4e243529dc6dd6406ee8579)) · [dark](connections-dark-phone.png) ([Stitch](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=80b6bb3f19c449829bd03c30c808b326)) |
