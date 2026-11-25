import type { MetadataRoute } from 'next';

function resolveSiteUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_APP_URL?.trim();
  if (explicit) return explicit.replace(/\/+$/, '');
  const vercelProd = process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  if (vercelProd) return `https://${vercelProd}`;
  const vercelUrl = process.env.VERCEL_URL?.trim();
  if (vercelUrl) return `https://${vercelUrl}`;
  return 'http://localhost:3000';
}

export default function robots(): MetadataRoute.Robots {
  const baseUrl = resolveSiteUrl();
  return {
    rules: [
      {
        userAgent: '*',
        allow: ['/', '/login', '/signup', '/forgot-password', '/playground'],
        disallow: ['/admin/', '/quota', '/settings', '/auth/'],
      },
    ],
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
