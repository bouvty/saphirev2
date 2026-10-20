import {NextRequest,NextResponse} from 'next/server';
export const dynamic='force-dynamic';
const api='https://saphire-python-api-production.up.railway.app';
export async function GET(req:NextRequest){
 const id=req.nextUrl.searchParams.get('discord_id')||'';
 const token=req.nextUrl.searchParams.get('token')||'';
 if(!/^\d{15,22}$/.test(id)||!/^[a-f0-9]{64}$/i.test(token))
  return NextResponse.json({error:'Invalid registration link. Request a new link from the Discord bot.'},{status:400,headers:{'Cache-Control':'no-store'}});
 try{
  const endpoint=new URL('/auth/start',process.env.RAILWAY_API_URL||api);
  endpoint.searchParams.set('discord_id',id);endpoint.searchParams.set('token',token);
  const upstream=await fetch(endpoint,{redirect:'manual',cache:'no-store',signal:AbortSignal.timeout(12000)});
  if(upstream.status===307||upstream.status===302){
   const location=upstream.headers.get('location');
   if(!location){return NextResponse.json({error:'Registration service response missing redirect'},{status:502})}
   const parsed=new URL(location);
   if(parsed.protocol!=='https:'||parsed.hostname!=='discord.com')
    return NextResponse.json({error:'Registration service returned an invalid redirect'},{status:502});
   const out=NextResponse.redirect(parsed,302);out.headers.set('Cache-Control','no-store');out.headers.set('Referrer-Policy','no-referrer');return out;
  }
  if(upstream.status===403)return NextResponse.json({error:'Invalid registration link. Restart the bot with the matching registration secret and request a new link.'},{status:403,headers:{'Cache-Control':'no-store'}});
  return NextResponse.json({error:'Registration service unavailable',status:upstream.status},{status:502,headers:{'Cache-Control':'no-store'}});
 }catch{return NextResponse.json({error:'Registration service unavailable'},{status:502})}
}
