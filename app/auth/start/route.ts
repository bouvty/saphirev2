import {NextRequest,NextResponse} from 'next/server';
export const dynamic='force-dynamic';
export async function GET(req:NextRequest){
 const id=req.nextUrl.searchParams.get('discord_id')||'';
 const token=req.nextUrl.searchParams.get('token')||'';
 if(!/^\d{15,22}$/.test(id)||!/^[0-9a-f]{64}$/i.test(token))
   return NextResponse.json({error:'Invalid or expired registration link. Request a new link from the Saphire Discord bot.'},{status:400,headers:{'Cache-Control':'no-store'}});
 const api=(process.env.RAILWAY_API_URL||'https://saphire-python-api-production.up.railway.app').replace(/\/$/,'');
 const url=new URL('/auth/start',api);url.searchParams.set('discord_id',id);url.searchParams.set('token',token);
 const response=NextResponse.redirect(url,307);response.headers.set('Cache-Control','no-store');response.headers.set('Referrer-Policy','no-referrer');return response;
}
