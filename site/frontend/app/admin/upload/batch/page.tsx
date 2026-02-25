'use client';

import { useState, useCallback, useRef } from 'react';
import Image from 'next/image';
import { Music, ImageIcon, Trash2, Loader2, Check, AlertCircle } from 'lucide-react';
import { getPresignedUrl, saveStoryToSupabase } from '@/app/actions/upload';

export type DraftStatus = 'idle' | 'uploading' | 'success' | 'error';

export type DraftStory = {
  id: string;
  audioFile: File;
  coverFile: File | null;
  coverPreview: string | null;
  title: string;
  genres: string[];
  tags: string;
  status: DraftStatus;
  errorMessage?: string;
};

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export default function BatchUploadPage() {
  const [stories, setStories] = useState<DraftStory[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [addMessage, setAddMessage] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const MAX_STORIES = 10;

  const handleAudioSelect = useCallback((files: FileList | null) => {
    if (!files?.length) return;
    const list = Array.from(files);
    const audioFiles = list.filter(
      (f) => f.type.startsWith('audio/') || /\.(mp3|wav|m4a|ogg|flac|aac|webm|opus)$/i.test(f.name)
    );
    const toAdd = audioFiles.length > 0 ? audioFiles : list;
    if (toAdd.length === 0) return;
    setStories((prev) => {
      const free = MAX_STORIES - prev.length;
      if (free <= 0) return prev;
      const slice = toAdd.slice(0, free);
      const newDrafts: DraftStory[] = slice.map((audioFile) => ({
        id: generateId(),
        audioFile,
        coverFile: null,
        coverPreview: null,
        title: audioFile.name.replace(/\.[^/.]+$/, ''),
        genres: [],
        tags: '',
        status: 'idle',
      }));
      const added = newDrafts.length;
      setTimeout(
        () =>
          setAddMessage(
            added > 0
              ? `Добавлено: ${added}. Макс. ${MAX_STORIES} рассказов за раз.${added < toAdd.length ? ' Часть не добавлена (лимит).' : ''}`
              : `Достигнут лимит ${MAX_STORIES} рассказов.`
          ),
        0
      );
      setTimeout(() => setAddMessage(null), 4000);
      return [...prev, ...newDrafts];
    });
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      handleAudioSelect(e.dataTransfer.files);
    },
    [handleAudioSelect]
  );

  const handleCoverSelect = useCallback((id: string, file: File | null) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) return;
    const preview = URL.createObjectURL(file);
    setStories((prev) =>
      prev.map((s) => {
        if (s.id !== id) return s;
        if (s.coverPreview) URL.revokeObjectURL(s.coverPreview);
        return { ...s, coverFile: file, coverPreview: preview };
      })
    );
  }, []);

  const updateDraft = useCallback(
    (id: string, patch: Partial<Pick<DraftStory, 'title' | 'genres' | 'tags'>>) => {
      setStories((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    },
    []
  );

  const removeDraft = useCallback((id: string) => {
    setStories((prev) => {
      const s = prev.find((x) => x.id === id);
      if (s?.coverPreview) URL.revokeObjectURL(s.coverPreview);
      return prev.filter((x) => x.id !== id);
    });
  }, []);

  const setDraftStatus = useCallback((id: string, status: DraftStatus, errorMessage?: string) => {
    setStories((prev) => prev.map((s) => (s.id === id ? { ...s, status, errorMessage } : s)));
  }, []);

  const tagsList = (tags: string) => tags.split(',').map((t) => t.trim()).filter(Boolean);
  const canPublish =
    stories.length > 0 &&
    stories.every(
      (s) =>
        s.coverFile &&
        s.title.trim() &&
        s.genres.length > 0 &&
        tagsList(s.tags).length >= 0
    );
  const hasIdle = stories.some((s) => s.status === 'idle' || s.status === 'error');

  const handlePublishAll = async () => {
    if (!canPublish || publishing) return;
    setPublishing(true);
    const toProcess = stories.filter((s) => s.status === 'idle' || s.status === 'error');
    const total = toProcess.length;
    setProgress({ current: 0, total });
    let done = 0;
    for (const story of toProcess) {
      if (!story.coverFile) continue;
      setDraftStatus(story.id, 'uploading');
      setProgress({ current: done + 1, total });
      try {
        const [coverPresign, audioPresign] = await Promise.all([
          getPresignedUrl(story.coverFile.name, story.coverFile.type),
          getPresignedUrl(story.audioFile.name, story.audioFile.type),
        ]);
        if ('error' in coverPresign) {
          setDraftStatus(story.id, 'error', coverPresign.error);
          continue;
        }
        if ('error' in audioPresign) {
          setDraftStatus(story.id, 'error', audioPresign.error);
          continue;
        }
        const [coverRes, audioRes] = await Promise.all([
          fetch(coverPresign.uploadUrl, {
            method: 'PUT',
            body: story.coverFile,
            headers: { 'Content-Type': story.coverFile.type },
          }),
          fetch(audioPresign.uploadUrl, {
            method: 'PUT',
            body: story.audioFile,
            headers: { 'Content-Type': story.audioFile.type },
          }),
        ]);
        if (!coverRes.ok) {
          setDraftStatus(story.id, 'error', 'Ошибка загрузки обложки в R2');
          continue;
        }
        if (!audioRes.ok) {
          setDraftStatus(story.id, 'error', 'Ошибка загрузки аудио в R2');
          continue;
        }
        const saveResult = await saveStoryToSupabase({
          title: story.title.trim(),
          genres: story.genres,
          image_url: coverPresign.publicUrl,
          audio_url: audioPresign.publicUrl,
          duration: 0,
          is_premium: false,
          tags: tagsList(story.tags),
        });
        if (!saveResult.success) {
          setDraftStatus(story.id, 'error', saveResult.error);
          continue;
        }
        setDraftStatus(story.id, 'success');
      } catch (err) {
        setDraftStatus(
          story.id,
          'error',
          err instanceof Error ? err.message : 'Неизвестная ошибка'
        );
      }
      done += 1;
      setProgress((p) => (p ? { ...p, current: done } : null));
    }
    setPublishing(false);
    setProgress(null);
  };

  const removeSuccess = () => {
    setStories((prev) => prev.filter((s) => s.status !== 'success'));
  };

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-semibold text-white mb-2">Пакетная загрузка историй</h1>
      <p className="text-sm text-zinc-500 mb-8">
        От 1 до 10 рассказов за раз: выберите аудиофайлы, для каждого задайте обложку, жанры и теги, затем опубликуйте.
      </p>

      {/* Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (stories.length < MAX_STORIES) setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        className={`mb-10 rounded-xl border-2 border-dashed flex flex-col items-center justify-center py-14 px-6 transition-colors ${
          stories.length >= MAX_STORIES
            ? 'border-zinc-700 bg-zinc-800/30 opacity-70'
            : dragActive
              ? 'border-cyan-500 bg-cyan-500/10'
              : 'border-zinc-700 bg-zinc-800/50'
        }`}
      >
        <Music className="w-14 h-14 text-zinc-500 mb-3" />
        <p className="text-zinc-400 mb-1">
          {stories.length >= MAX_STORIES ? `Достигнут лимит (${MAX_STORIES})` : 'Выберите аудиофайлы (до 10)'}
        </p>
        <p className="text-sm text-zinc-500 mb-4">MP3, WAV и др. — макс. 10 рассказов за раз</p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac,.aac,.webm,.opus"
          className="hidden"
          onChange={(e) => {
            handleAudioSelect(e.target.files);
            e.target.value = '';
          }}
          disabled={stories.length >= MAX_STORIES}
        />
        <button
          type="button"
          onClick={() => stories.length < MAX_STORIES && fileInputRef.current?.click()}
          disabled={stories.length >= MAX_STORIES}
          className="px-5 py-2.5 rounded-lg bg-cyan-600 text-white font-medium hover:bg-cyan-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Выбрать файлы
        </button>
      </div>

      {addMessage && (
        <p className="mb-6 text-sm text-emerald-400" role="status">
          {addMessage}
        </p>
      )}

      {/* Overall progress */}
      {progress && (
        <div className="mb-6 p-4 rounded-xl bg-zinc-800/80 border border-zinc-700">
          <p className="text-sm text-zinc-300 mb-2">
            Загружаем <strong>{progress.current}</strong> из <strong>{progress.total}</strong>
          </p>
          <div className="h-2 rounded-full bg-zinc-700 overflow-hidden">
            <div
              className="h-full bg-cyan-500 transition-all duration-300"
              style={{ width: `${progress.total ? (100 * progress.current) / progress.total : 0}%` }}
            />
          </div>
        </div>
      )}

      {/* Drafts list */}
      {stories.length > 0 && (
        <>
          <h2 className="text-lg font-medium text-white mb-4">
            Черновики ({stories.length})
          </h2>
          <div className="space-y-4 mb-8">
            {stories.map((story) => (
              <DraftCard
                key={story.id}
                story={story}
                onCoverSelect={(file) => handleCoverSelect(story.id, file)}
                onUpdate={(patch) => updateDraft(story.id, patch)}
                onRemove={() => removeDraft(story.id)}
              />
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={handlePublishAll}
              disabled={!canPublish || publishing}
              className="px-6 py-3 rounded-lg bg-cyan-600 text-white font-medium hover:bg-cyan-500 transition-colors disabled:opacity-50 disabled:pointer-events-none"
            >
              {publishing ? (
                <>
                  <Loader2 className="w-4 h-4 inline-block animate-spin mr-2 -mt-0.5" />
                  Загрузка...
                </>
              ) : (
                'Опубликовать все'
              )}
            </button>
            {stories.some((s) => s.status === 'success') && (
              <button
                type="button"
                onClick={removeSuccess}
                className="text-sm text-zinc-400 hover:text-white transition-colors"
              >
                Убрать успешные из списка
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

type DraftCardProps = {
  story: DraftStory;
  onCoverSelect: (file: File) => void;
  onUpdate: (patch: Partial<Pick<DraftStory, 'title' | 'genres' | 'tags'>>) => void;
  onRemove: () => void;
};

function DraftCard({ story, onCoverSelect, onUpdate, onRemove }: DraftCardProps) {
  const coverInputRef = useRef<HTMLInputElement>(null);
  const genreInputRef = useRef<HTMLInputElement>(null);
  const isDisabled = story.status === 'uploading' || story.status === 'success';

  const handleAddGenre = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = (e.target as HTMLInputElement).value.trim();
    if (!v) return;
    if (story.genres.includes(v)) return;
    onUpdate({ genres: [...story.genres, v] });
    (e.target as HTMLInputElement).value = '';
  };

  const borderColor =
    story.status === 'success'
      ? 'border-emerald-500/60 bg-emerald-950/30'
      : story.status === 'error'
        ? 'border-rose-500/60 bg-rose-950/20'
        : 'border-zinc-700 bg-zinc-800/50';

  return (
    <div
      className={`rounded-xl border-2 p-5 transition-colors ${borderColor}`}
      aria-busy={story.status === 'uploading'}
    >
      <div className="flex flex-wrap gap-5">
        {/* Cover block */}
        <div className="flex flex-col">
          <span className="text-xs text-zinc-500 mb-1.5">Обложка</span>
          <div className="w-28 h-28 rounded-lg border border-zinc-700 bg-zinc-900 overflow-hidden flex items-center justify-center">
            {story.coverPreview ? (
              <Image
                src={story.coverPreview}
                alt=""
                width={112}
                height={112}
                className="object-cover w-full h-full"
                unoptimized
              />
            ) : (
              <ImageIcon className="w-10 h-10 text-zinc-600" />
            )}
          </div>
          <input
            ref={coverInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onCoverSelect(f);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            onClick={() => coverInputRef.current?.click()}
            disabled={isDisabled}
            className="mt-2 text-xs text-cyan-500 hover:text-cyan-400 disabled:opacity-50"
          >
            {story.coverFile ? 'Заменить обложку' : 'Загрузить обложку'}
          </button>
        </div>

        {/* Fields */}
        <div className="flex-1 min-w-[200px] space-y-3">
          <p className="text-sm text-zinc-400 truncate" title={story.audioFile.name}>
            <Music className="w-3.5 h-3.5 inline-block mr-1 -mt-0.5" />
            {story.audioFile.name}
          </p>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Название</label>
            <input
              type="text"
              value={story.title}
              onChange={(e) => onUpdate({ title: e.target.value })}
              disabled={isDisabled}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-white text-sm placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none disabled:opacity-70"
              placeholder="Название"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Жанры (несколько)</label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {story.genres.map((g) => (
                <span
                  key={g}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-cyan-600/30 text-cyan-200 text-xs"
                >
                  {g}
                  <button
                    type="button"
                    onClick={() => onUpdate({ genres: story.genres.filter((x) => x !== g) })}
                    disabled={isDisabled}
                    className="hover:text-white disabled:opacity-50"
                    aria-label={`Удалить ${g}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <input
              ref={genreInputRef}
              type="text"
              disabled={isDisabled}
              onKeyDown={handleAddGenre}
              placeholder="Введите жанр и Enter"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-white text-sm placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none disabled:opacity-70"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">Теги (через запятую)</label>
            <input
              type="text"
              value={story.tags}
              onChange={(e) => onUpdate({ tags: e.target.value })}
              disabled={isDisabled}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-white text-sm placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none disabled:opacity-70"
              placeholder="тег1, тег2, тег3"
            />
          </div>
        </div>

        {/* Status & actions */}
        <div className="flex flex-col items-end justify-between gap-2">
          {story.status === 'uploading' && (
            <span className="flex items-center gap-2 text-cyan-400 text-sm">
              <Loader2 className="w-5 h-5 shrink-0 animate-spin" aria-hidden />
              Загружается…
            </span>
          )}
          {story.status === 'success' && (
            <span className="flex items-center gap-1 text-emerald-400 text-sm">
              <Check className="w-4 h-4" /> Готово
            </span>
          )}
          {story.status === 'error' && (
            <span className="flex items-center gap-1 text-rose-400 text-sm max-w-[180px]" title={story.errorMessage}>
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span className="truncate">{story.errorMessage || 'Ошибка'}</span>
            </span>
          )}
          <button
            type="button"
            onClick={onRemove}
            disabled={story.status === 'uploading'}
            className="mt-2 p-2 rounded-lg text-zinc-400 hover:text-rose-400 hover:bg-zinc-700/50 transition-colors disabled:opacity-50"
            aria-label="Удалить из списка"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
