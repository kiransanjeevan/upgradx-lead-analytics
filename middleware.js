// Basic Auth gate for the whole site (HTML + data), enforced at Vercel's edge.
// Credentials are read from Vercel env vars DASHBOARD_USER / DASHBOARD_PASS.
// Basic Auth travels over HTTPS, so it's fine for an internal dashboard.
export const config = {
  // gate everything except Vercel internals / favicon
  matcher: ['/((?!_next/|favicon.ico).*)'],
};

export default function middleware(request) {
  const auth = request.headers.get('authorization');
  const USER = process.env.DASHBOARD_USER;
  const PASS = process.env.DASHBOARD_PASS;

  if (auth && auth.startsWith('Basic ')) {
    let decoded = '';
    try { decoded = atob(auth.slice(6)); } catch (e) { decoded = ''; }
    const i = decoded.indexOf(':');
    const user = decoded.slice(0, i);
    const pass = decoded.slice(i + 1);
    if (i !== -1 && user === USER && pass === PASS) {
      return; // authorized -> continue to the static asset
    }
  }

  return new Response('Authentication required.', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="upGradX Lead Analytics"' },
  });
}
