import type { NextConfig } from 'next';
const config: NextConfig = { output: 'standalone',
 async rewrites(){return process.env.LOCAL_BACKEND_URL?[
  {source:'/api/:path*',destination:process.env.LOCAL_BACKEND_URL+'/api/:path*'},
  {source:'/auth/:path*',destination:(process.env.LOCAL_KEYCLOAK_URL||'http://localhost:8080')+'/auth/:path*'}
 ]:[];}
};
export default config;
