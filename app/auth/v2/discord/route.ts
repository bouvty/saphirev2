import {NextResponse} from 'next/server';
export function GET(){return NextResponse.redirect((process.env.NEXT_PUBLIC_SITE_URL||'https://saphire.rip').replace(/\/$/,'')+'/auth/v2/discord/denied/',303)}
