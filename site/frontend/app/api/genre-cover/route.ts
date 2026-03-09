import { NextRequest, NextResponse } from 'next/server';
import { readFile, readdir } from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GENRES_DIR = path.resolve(__dirname, '..', '..', '..', 'public', 'genres');

const GENRES_BASES = [
  GENRES_DIR,
  path.join(process.cwd(), 'public', 'genres'),
  path.join(process.cwd(), 'frontend', 'public', 'genres'),
  path.join(process.cwd(), 'site', 'frontend', 'public', 'genres'),
];

const COVER_NAMES = ['cover.jpg', 'cover.JPG', 'cover.jpeg'];

function safePath(base: string, sub: string): string {
  const resolved = path.resolve(base, sub);
  return path.resolve(resolved).startsWith(path.resolve(base)) ? resolved : '';
}

/**
 * GET /api/genre-cover?genre=... — отдаёт cover для жанра.
 */
export async function GET(request: NextRequest) {
  const genre = request.nextUrl.searchParams.get('genre');
  if (!genre || typeof genre !== 'string') {
    return NextResponse.json({ error: 'Missing genre' }, { status: 400 });
  }
  const decoded = decodeURIComponent(genre);
  if (decoded.includes('..') || decoded.includes('\0')) {
    return NextResponse.json({ error: 'Invalid genre' }, { status: 400 });
  }

  const tried: string[] = [];

  for (const base of GENRES_BASES) {
    const dirPath = safePath(base, decoded);
    if (!dirPath) continue;
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
    // Если по точному имени не нашли — сравниваем с реальными папками (нормализация)
    try {
      const folders = await readdir(base, { withFileTypes: true });
      const match = folders
        .filter((d) => d.isDirectory())
        .find((d) => d.name === decoded || d.name.normalize('NFC') === decoded.normalize('NFC'));
      if (match) {
        const dirPath = path.join(base, match.name);
        for (const name of COVER_NAMES) {
          const filePath = path.join(dirPath, name);
          tried.push(filePath);
          try {
            const buf = await readFile(filePath);
            return new NextResponse(buf, {
              headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=86400' },
            });
          } catch {
            continue;
          }
        }
      }
    } catch {
      // база не существует
    }
  }

  if (process.env.NODE_ENV === 'development') {
    console.warn('[genre-cover] Not found:', decoded, 'tried:', tried);
  }
  return NextResponse.json(
    { error: 'Not found', tried: process.env.NODE_ENV === 'development' ? tried : undefined },
    { status: 404 }
  );
}
