const PROTECTED_HOST = "dashboard.supplyintel.org";
const PAGES_HOST = "supply-intel-watcher.pages.dev";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const isPagesDev =
      url.hostname === PAGES_HOST || url.hostname.endsWith(`.${PAGES_HOST}`);

    if (isPagesDev) {
      url.protocol = "https:";
      url.hostname = PROTECTED_HOST;
      url.port = "";
      return Response.redirect(url.toString(), 307);
    }

    const assetResponse = await env.ASSETS.fetch(request);
    const response = new Response(assetResponse.body, assetResponse);

    if (url.pathname === "/" || url.pathname === "/index.html") {
      response.headers.set(
        "Cache-Control",
        "no-store, no-cache, must-revalidate, max-age=0"
      );
      response.headers.set("Pragma", "no-cache");
      response.headers.set("Expires", "0");
    }

    return response;
  },
};
