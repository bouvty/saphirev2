import {NextRequest,NextResponse} from 'next/server';
export function middleware(request:NextRequest){const p=request.nextUrl.pathname;
 if(p==='/'||p==='/404'||p.startsWith('/v2/api/')||p==='/v2/members/order/redeem')return NextResponse.next();
 const url=request.nextUrl.clone();url.pathname='/404';url.search='';return NextResponse.rewrite(url,{status:404});}
export const config={matcher:['/((?!_next/|favicon.ico|robots.txt|sitemap.xml).*)']};