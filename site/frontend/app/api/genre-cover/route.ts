import { NextRequest, NextResponse } from 'next/server';
import { readFile } from 'fs/promises';
import path from 'path';

/** Возможные корни в зависимости от того, откуда запущен next (cwd) */
const GENRES_BASES = [
  path.join(process.cwd(), 'public', 'genres'),
  path.join(process.cwd(), 'frontend', 'public', 'genres'),
  path.join(process.cwd(), 'site', 'frontend', 'public', 'genres'),
];

const COVER_NAMES = ['cover.jpg', 'cover.JPG', 'cover.jpeg'];

/**
 * GET /api/genre-cover?genre=... — отдаёт cover для жанра.
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

  const tried: string[] = [];
  for (const base of GENRES_BASES) {
    const dirPath = path.join(base, decoded);
    if (!path.resolve(dirPath).startsWith(path.resolve(base))) continue;
    for (const name of COVER_NAMES) {
      const filePath = path.join(dirPath, name);
      tried.push(filePath);
      try {
        const buf = await readFile(filePath);
        return new NextResponse(buf, {
          headers: {
            'Content-Type': 'image/jpeg',
            'Cache-Control': 'public, max-age=86400',
          },
        });
      } catch {
        continue;
      }
    }
  }

  if (process.env.NODE_ENV === 'development') {
    console.warn('[genre-cover] Not found:', decoded, 'tried:', tried);
  }
  return NextResponse.json({ error: 'Not found', tried: process.env.NODE_ENV === 'development' ? tried : undefined }, { status: 404 });
}
