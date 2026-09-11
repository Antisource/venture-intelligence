// Optional public-page renderer. Install with: npm install.
// It does not authenticate, bypass CAPTCHAs, or access private pages.
import { log, LogLevel, PlaywrightCrawler } from 'crawlee';

// stdout is the machine-readable IPC channel consumed by Python. Crawlee's
// human-oriented run logs must not corrupt the single JSON document.
log.setLevel(LogLevel.OFF);

const url = process.argv[2];
let result = { ok: false, status_code: null, text: '', error: 'no URL' };
if (url) {
  const initial = new URL(url);
  let blockedReason = null;
  const crawler = new PlaywrightCrawler({
    maxRequestsPerCrawl: 1,
    maxConcurrency: 1,
    maxRequestRetries: 0,
    requestHandlerTimeoutSecs: 15,
    navigationTimeoutSecs: 15,
    headless: true,
    // Every run uses an unauthenticated ephemeral browser context. Do not
    // retain cookies or session state across public collection requests.
    useSessionPool: false,
    persistCookiesPerSession: false,
    preNavigationHooks: [async ({ page }) => {
      await page.route('**/*', async (route) => {
        const request = route.request();
        const requested = new URL(request.url());
        const initialNavigation = request.isNavigationRequest()
          && request.frame() === page.mainFrame()
          && request.redirectedFrom() === null
          && requested.protocol === initial.protocol
          && requested.hostname.toLowerCase().replace(/\.$/, '')
            === initial.hostname.toLowerCase().replace(/\.$/, '')
          && requested.port === initial.port;
        if (initialNavigation) {
          await route.continue();
          return;
        }
        blockedReason = 'rendered redirects and subresources are blocked for bounded public crawling';
        await route.abort();
      });
    }],
    requestHandler: async ({ page, response }) => {
      const finalUrl = new URL(page.url());
      if (
        finalUrl.protocol !== initial.protocol
        || finalUrl.hostname.toLowerCase().replace(/\.$/, '')
          !== initial.hostname.toLowerCase().replace(/\.$/, '')
        || finalUrl.port !== initial.port
      ) {
        result = {
          ok: false,
          status_code: response?.status() ?? null,
          final_url: page.url(),
          text: '',
          error: 'rendered cross-host navigation blocked by source policy',
        };
        return;
      }
      result = {
        ok: true,
        status_code: response?.status() ?? 200,
        final_url: page.url(),
        text: (await page.locator('body').innerText()).slice(0, 1000000),
        error: null,
      };
    },
    failedRequestHandler: async ({ error }) => {
      result = {
        ok: false,
        status_code: null,
        text: '',
        error: blockedReason ?? String(error),
      };
    },
  });
  await crawler.run([{ url }]);
}
process.stdout.write(JSON.stringify(result));
