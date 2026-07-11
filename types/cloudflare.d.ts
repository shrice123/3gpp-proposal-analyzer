interface Fetcher {
  fetch(request: Request): Promise<Response>;
}

// The full runtime shape is supplied by Wrangler; the desktop build never calls it.
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
interface D1Database {}

declare module "cloudflare:workers" {
  export const env: { DB?: D1Database };
}
