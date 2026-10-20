import {NextRequest,NextResponse} from 'next/server';
export function middleware(req:NextRequest){const host=(req.headers.get('x-forwarded-host')||req.headers.get('host')||'').split(':')[0].toLowerCase();if(host==='tickets.saphire.rip'&&req.nextUrl.pathname==='/'){const u=req.nextUrl.clone();u.pathname='/login';return NextResponse.rewrite(u)}return NextResponse.next()}
export const config={matcher:['/']};
