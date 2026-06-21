/**
 * Campaign Utility Tool walkthrough login — the campaign analog of
 * ace-labs-walkthrough-login. Mirrors the labs flow exactly:
 *   labs:     hqOAuthLogin (CommCare-HQ session) -> labsOAuthLogin click-through -> sessionid -> browse import
 *   campaign: reuse that CommCare-HQ session       -> campaign  click-through     -> sessionid -> browse import
 *
 * The campaign app (/campaign/) uses CommCare-HQ OAuth (www.commcarehq.org/oauth/authorize/,
 * scope access_apis, PKCE). Because ~/.ace/labs-session.json already carries the
 * www.commcarehq.org cookies (the labs flow ran hqOAuthLogin), driving the campaign
 * login CTA silently re-grants and lands back on /campaign/ with the campaign session set.
 *
 * Prints {sessionDomain, sessionName, sessionValue} JSON so the shell can import the
 * cookie into the gstack browse profile via `browse cookie-import`.
 */
import { chromium } from 'playwright';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const LABS = process.env.CAMPAIGN_LABS_BASE || 'https://labs.connect.dimagi.com';
const STATE = path.join(os.homedir(), '.ace', 'labs-session.json');

async function main() {
  if (!fs.existsSync(STATE)) {
    throw new Error(`no ${STATE} — run /ace:labs-login first to establish the CommCare-HQ session`);
  }
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ storageState: STATE });
    const page = await context.newPage();

    // Initiate the campaign OAuth. With the CommCare-HQ session present, the
    // authorize endpoint silently re-grants and bounces back to /campaign/.
    await page.goto(`${LABS}/campaign/login/initiate/`, { waitUntil: 'domcontentloaded' });

    // Land on /campaign/ (success) or a one-time CommCare consent page.
    await page.waitForURL(
      (u) => {
        const url = new URL(u);
        return (
          (url.hostname === new URL(LABS).hostname && url.pathname.startsWith('/campaign')) ||
          url.hostname.endsWith('commcarehq.org')
        );
      },
      { timeout: 45_000 },
    );

    // If a CommCare consent prompt appeared, click through it.
    if (new URL(page.url()).hostname.endsWith('commcarehq.org')) {
      const allow = page
        .locator(
          'input[name="allow"], button:has-text("Authorize"), button:has-text("Allow"), input[value="Authorize"], button[type="submit"]',
        )
        .first();
      if ((await allow.count()) > 0) {
        await Promise.all([
          page.waitForURL((u) => new URL(u).pathname.startsWith('/campaign'), { timeout: 45_000 }),
          allow.click(),
        ]);
      }
    }

    // Confirm we're authed: the app shell (not the login page).
    await page.goto(`${LABS}/campaign/`, { waitUntil: 'domcontentloaded' });
    const onLogin = page.url().includes('/campaign/login');
    if (onLogin) throw new Error('still on /campaign/login after OAuth — consent or whitelist failed');

    const cookies = await context.cookies();
    const host = new URL(LABS).hostname; // labs.connect.dimagi.com
    // Match the LABS sessionid specifically — the context also holds a
    // connect.dimagi.com sessionid, which is NOT the campaign session.
    const sid = cookies.find((c) => c.name === 'sessionid' && c.domain.includes(host));
    if (!sid) throw new Error('no labs sessionid cookie after campaign login');

    // Persist the updated state (campaign session) back for reuse.
    await context.storageState({ path: STATE });
    process.stdout.write(
      JSON.stringify({ sessionDomain: sid.domain, sessionName: sid.name, sessionValue: sid.value, host }) + '\n',
    );
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  process.stderr.write(`[campaign-login] FAILED: ${e.message}\n`);
  process.exit(1);
});
