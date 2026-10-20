import {NextRequest,NextResponse} from 'next/server';
import {jwtVerify} from 'jose';
const ticketHost='tickets.saphire.rip';
const mainHost='saphire.rip';
function hostOf(req:NextRequest){return (req.headers.get('host')||'').split(':')[0].toLowerCase()}
function missing(req:NextRequest){const target=req.nextUrl.clone();target.pathname='/404';target.search='';return NextResponse.rewrite(target,{status:404})}
export async function middleware(req:NextRequest){
 const host=hostOf(req),path=req.nextUrl.pathname,isTickets=host===ticketHost,isMain=host===mainHost;
 // The maintenance site never presents a login form or ticket records.
 if(!isTickets&&isMain&&(path==='/login'||path.startsWith('/tickets/')||path==='/api/auth/login'))return missing(req);
 if(!isTickets)return NextResponse.next();
 if(path==='/'){const u=req.nextUrl.clone();u.pathname='/login';return NextResponse.rewrite(u)}
 if(path==='/login')return NextResponse.redirect(new URL('/',req.url),307);
 if(path==='/api/auth/login'){
  // OAuth starts only on the dedicated tickets host.
  return NextResponse.next();
 }
 if(path.startsWith('/tickets/')){
  const token=req.cookies.get('saphire_session')?.value,secret=process.env.SESSION_SECRET;
  let valid=false;
  if(token&&secret&&secret.length>=32){try{const {payload}=await jwtVerify(token,new TextEncoder().encode(secret),{algorithms:['HS256']});valid=typeof payload.id==='string'&&/^\d{15,22}$/.test(payload.id)}catch{}}
  if(!valid){const u=new URL('/',req.url);u.searchParams.set('next',path);return NextResponse.redirect(u,307)}
 }
 return NextResponse.next()
}
export const config={matcher:['/','/login','/api/auth/login','/tickets/:path*']};
