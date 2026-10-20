import {NextRequest,NextResponse} from 'next/server';import {jwtVerify} from 'jose';
export async function middleware(req:NextRequest){
 const host=(req.headers.get('x-forwarded-host')||req.headers.get('host')||'').split(':')[0].toLowerCase();
 if(host!=='tickets.saphire.rip')return NextResponse.next();
 const path=req.nextUrl.pathname;
 if(path==='/'){const u=req.nextUrl.clone();u.pathname='/login';return NextResponse.rewrite(u)}
 if(path==='/login'){const u=req.nextUrl.clone();u.pathname='/';return NextResponse.redirect(u,307)}
 if(path.startsWith('/tickets/')){
   const token=req.cookies.get('saphire_session')?.value,secret=process.env.SESSION_SECRET;
   let valid=false;
   if(token&&secret&&secret.length>=32){try{await jwtVerify(token,new TextEncoder().encode(secret));valid=true}catch{}}
   if(!valid){const u=req.nextUrl.clone();u.pathname='/';u.search='';u.searchParams.set('next',path);return NextResponse.redirect(u,307)}
 }
 return NextResponse.next()
}
export const config={matcher:['/','/login','/tickets/:path*']};