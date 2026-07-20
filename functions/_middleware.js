const PROTECTED_HOST = "dashboard.supplyintel.org";
const PAGES_HOST = "supply-intel-watcher.pages.dev";

export async function onRequest(context) {
  const url = new URL(context.request.url);
  const isPagesDev =
    url.hostname === PAGES_HOST || url.hostname.endsWith(`.${PAGES_HOST}`);

  if (isPagesDev) {
    url.protocol = "https:";
    url.hostname = PROTECTED_HOST;
    url.port = "";
    return Response.redirect(url.toString(), 307);
  }

  return context.next();
}
