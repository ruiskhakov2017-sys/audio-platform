'use server';

import { PutObjectCommand, DeleteObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { createClient } from '@supabase/supabase-js';
import { r2, R2_BUCKET, R2_PUBLIC_URL } from '@/lib/r2';

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_SERVICE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export type GetPresignedUrlResult =
  | { uploadUrl: string; key: string; publicUrl: string }
  | { error: string };

export async function getPresignedUrl(
  fileName: string,
  fileType: string
): Promise<GetPresignedUrlResult> {
  if (!r2) {
    return {
      error:
        'R2 не настроен. Проверьте R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY в .env.local',
    };
  }
  const key = `${Date.now()}-${fileName.replace(/\s/g, '-')}`;
  const command = new PutObjectCommand({
    Bucket: R2_BUCKET,
    Key: key,
    ContentType: fileType,
  });
  try {
    const uploadUrl = await getSignedUrl(r2, command, { expiresIn: 600 });
    const base = R2_PUBLIC_URL.replace(/\/$/, '');
    const publicUrl = `${base}/${key}`;
    return { uploadUrl, key, publicUrl };
  } catch (e) {
    const message = e instanceof Error ? e.message : 'Ошибка подписи URL';
    return { error: message };
  }
}

export type SaveStoryPayload = {
  title: string;
  genres: string[];
  image_url: string;
  audio_url: string;
  duration: number;
  is_premium: boolean;
  description?: string;
  tags?: string[];
};

export type SaveStoryResult = { success: true; id?: number } | { success: false; error: string };

export async function saveStoryToSupabase(payload: SaveStoryPayload): Promise<SaveStoryResult> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) {
    return { success: false, error: 'Supabase не настроен' };
  }
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const row: Record<string, unknown> = {
    title: payload.title,
    image_url: payload.image_url,
    audio_url: payload.audio_url,
    duration: payload.duration,
    is_premium: payload.is_premium,
  };
  row.author = '';
  if (payload.genres?.length) {
    row.genres = payload.genres;
    row.genre = payload.genres[0] ?? null;
  }
  if (payload.description != null) row.description = payload.description;
  if (payload.tags != null) row.tags = payload.tags;
  const { data, error } = await supabase.from('stories').insert(row).select('id').single();
  if (error) {
    return { success: false, error: error.message };
  }
  return { success: true, id: data?.id };
}

export type UpdateStoryGenreResult = { success: true } | { success: false; error: string };

export async function updateStoryGenresAndTags(
  id: number,
  genres: string[] | undefined,
  tags: string[] | undefined
): Promise<UpdateStoryGenreResult> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) {
    return { success: false, error: 'Supabase не настроен' };
  }
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const updates: Record<string, unknown> = {};
  if (genres !== undefined) updates.genres = genres;
  if (tags !== undefined) updates.tags = tags;
  if (Object.keys(updates).length === 0) return { success: true };
  const { error } = await supabase.from('stories').update(updates).eq('id', id);
  if (error) return { success: false, error: error.message };
  return { success: true };
}

export type DeleteStoryResult = { success: true } | { success: false; error: string };

export async function deleteStory(id: number): Promise<DeleteStoryResult> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) {
    return { success: false, error: 'Supabase не настроен' };
  }
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const { data: row, error: fetchErr } = await supabase
    .from('stories')
    .select('audio_url')
    .eq('id', id)
    .single();
  if (fetchErr || !row) return { success: false, error: fetchErr?.message ?? 'История не найдена' };

  const audioUrl = row.audio_url as string | null;
  if (audioUrl && r2) {
    try {
      const u = new URL(audioUrl);
      const key = u.pathname.replace(/^\//, '');
      if (key) {
        await r2.send(new DeleteObjectCommand({ Bucket: R2_BUCKET, Key: key }));
      }
    } catch {
      // R2 delete failed — continue with DB delete
    }
  }

  const { error: deleteErr } = await supabase.from('stories').delete().eq('id', id);
  if (deleteErr) return { success: false, error: deleteErr.message };
  return { success: true };
}
