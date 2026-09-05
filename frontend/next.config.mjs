/** @type {import('next').NextConfig} */
const nextConfig = {
  // Export statique : le build est servi directement par FastAPI, sans
  // second process Node en production. `npm run dev` reste disponible.
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: true,
};
export default nextConfig;
