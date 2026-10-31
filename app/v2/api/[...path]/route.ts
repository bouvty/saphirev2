import {NextRequest,NextResponse} from 'next/server';
export const runtime='nodejs';export const dynamic='force-dynamic';
const upstream=process.env.RAILWAY_API_URL||'https://saphire-python-api-production.up.railway.app';
const aliases:Record<string,string>={
 'health':'/health','auth/start':'/auth/start','auth/callback':'/auth/callback',
 'auth/v2/discord':'/auth/v2/discord','auth/discord/callback':'/auth/v2/discord/callback',
 'auth/discord/internal/success':'/internal/auth/v2/discord/success',
 'internal/tickets':'/internal/tickets','auth/login':'/auth/start',
 'members/stock':'/members/stock',
 'resellers/members/order':'/resellers/members/order',
 'members/order/redeem':'/members/order/redeem',
 'members/order':'/members/order'
};
function mapPath(parts:string[]){const path=parts.join('/');if(path==='auth/discord/'||path==='auth/discord')return '/auth/v2/discord';
 for(const [key,value] of Object.entries(aliases).sort((a,b)=>b[0].length-a[0].length)){if(path===key||path.startsWith(key+'/'))return value+path.slice(key.length)}return null}
async function handler(req:NextRequest,{params}:{params:Promise<{path:string[]}>}){
 const {path}=await params;const mapped=mapPath(path||[]);
 if(!mapped)return NextResponse.json({error:'Not found'},{status:404});
 const callback=mapped==='/auth/callback'||mapped==='/auth/v2/discord/callback';
 const inviteFlow=mapped==='/auth/start'||mapped==='/auth/v2/discord'||mapped.startsWith('/auth/v2/discord/');
 // OAuth callback is authorized by an expiring, single-use state, not a public API token.
 const expected=process.env.SAPHIRE_V2_API_KEY;
 if(!callback&&!inviteFlow&&(!expected||req.headers.get('x-api-key')!==expected))
  return NextResponse.json({error:'Invalid or missing API key'},{status:401,headers:{'Cache-Control':'no-store'}});
 if(mapped.startsWith('/internal/')&&!req.headers.get('authorization'))
  return NextResponse.json({error:'Internal authorization required'},{status:403});
 const target=new URL(mapped,upstream);target.search=req.nextUrl.search;
 try{
  const headers=new Headers({'accept':'application/json'});
  const authorization=req.headers.get('authorization');if(authorization)headers.set('authorization',authorization);
  if(expected)headers.set('x-api-key',expected);
  const body=req.method==='POST'?await req.arrayBuffer():undefined;
  if(body){headers.set('content-type',req.headers.get('content-type')||'application/json')}
  const response=await fetch(target,{method:req.method,headers,body,redirect:'manual',cache:'no-store',signal:AbortSignal.timeout(20000)});
  const resultHeaders=new Headers({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
  const contentType=response.headers.get('content-type');if(contentType)resultHeaders.set('Content-Type',contentType);
  const location=response.headers.get('location');if(location&&response.status>=300&&response.status<400){
   const dest=new URL(location,target);
   if(dest.protocol!=='https:'||(!['discord.com','www.discord.com','saphire.rip'].includes(dest.hostname)))return NextResponse.json({error:'Invalid redirect'},{status:502});
   resultHeaders.set('Location',dest.toString());
  }
  return new NextResponse(response.status===204?null:await response.arrayBuffer(),{status:response.status,headers:resultHeaders});
 }catch{return NextResponse.json({error:'API temporarily unavailable'},{status:502})}
}
export {handler as GET,handler as POST};
