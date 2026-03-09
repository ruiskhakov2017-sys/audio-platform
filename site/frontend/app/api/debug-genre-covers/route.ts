import { NextResponse } from 'next/server';
import { readdir } from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GENRES_DIR = path.resolve(__dirname, '..', '..', '..', 'public', 'genres');

/** GET /api/debug-genre-covers — показывает, какие папки жанров видит API */
export async function GET() {
  const cwd = process.cwd();
  let folders: string[] = [];
  let error = '';
  try {
    folders = await readdir(GENRES_DIR, { withFileTypes: true }).then((d) =>
      d.filter((e) => e.isDirectory()).map((e) => e.name)
    );
  } catch (e) {
    error = String(e);
  }
  return NextResponse.json({
    genresDir: GENRES_DIR,
    cwd,
    folderCount: folders.length,
    folders,
    error: error || undefined,
  });
}
