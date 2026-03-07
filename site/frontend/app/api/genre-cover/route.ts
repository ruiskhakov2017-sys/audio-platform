import { NextRequest, NextResponse } from 'next/server';
import { readFile } from 'fs/promises';
import path from 'path';

const GENRES_DIR = path.join(process.cwd(), 'public', 'genres');

/**
 * GET /api/genre-cover?genre=... — отдаёт cover.jpg для жанра.
 * Обходит проблему 404 с Unicode/пробелами в путях статики Next.js.
 */
export async function GET(request: NextRequest) {
  const genre = request.nextUrl.searchParams.get('genre');
  if (!genre || typeof genre !== 'string') {
    return NextResponse.json({ error: 'Missing genre' }, { status: 400 });
  }
  const decoded = decodeURIComponent(genre);
  if (decoded.includes('..') || path.isAbsolute(decoded) || decoded.includes('\0')) {
    return NextResponse.json({ error: 'Invalid genre' }, { status: 400 });
  }
  const filePath = path.join(GENRES_DIR, decoded, 'cover.jpg');
  if (!filePath.startsWith(GENRES_DIR)) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 });
  }
  try {
    const buf = await readFile(filePath);
    return new NextResponse(buf, {
      headers: {
        'Content-Type': 'image/jpeg',
        'Cache-Control': 'public, max-age=86400',
      },
    });
  } catch {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }
}
