# Render prerequisites — status 2026-06-20

- G1 (national data): RESOLVED — synthetic_env_ensure(env=campaign-utility-tool) → MR-NAT-2026
  live on labs (5000 workers / 37 states / 773 microplans). PR #700. Reproducible via the env framework.
- G2 (recorder CommCare-OAuth): OPEN. /ace:labs-login refreshes only the labs Connect session
  (labs.connect.dimagi.com sessionid). The campaign app at /campaign/ does its OWN CommCare-HQ
  OAuth bounce → it needs the campaign session cookie, not the labs one. canopy:walkthrough does
  not know the campaign's OAuth path. Earlier this session, /campaign/ was driven authed via
  Playwright + ACE_HQ creds — that path works but is NOT wired into the recorder.
- BUNDLE: verify the deployed campaign JSX bundle is current (map #696 / reporting #695) before
  recording — deploy skips webpack (node-build blindspot, path filter fixed #699).
