'use client';

import { useState, useEffect, useCallback } from 'react';
import Image from 'next/image';
import { X, Loader2, ImageIcon, Music, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { getPresignedUrl, updateStory } from '@/app/actions/upload';
import type { Story } from '@/types/story';

type ChipInputProps = {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  label: string;
};

const ChipInput = ({ value, onChange, placeholder, label }: ChipInputProps) => {
  const [input, setInput] = useState('');

  const addCurrent = useCallback(() => {
    const t = input.trim();
    if (!t) return;
    if (value.includes(t)) {
      setInput('');
      return;
    }
    onChange([...value, t]);
    setInput('');
  }, [input, value, onChange]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addCurrent();
    } else if (e.key === 'Backspace' && !input && value.length) {
      onChange(value.slice(0, -1));
    }
  };

  const remove = (i: number) => {
    onChange(value.filter((_, idx) => idx !== i));
  };

  return (
    <div>
      <label className="block text-sm font-medium text-zinc-400 mb-1">{label}</label>
      <div className="flex flex-wrap gap-2 p-2 rounded-lg border border-zinc-700 bg-zinc-900 min-h-[42px]">
        {value.map((item, i) => (
          <span
            key={`${item}-${i}`}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 text-sm border border-cyan-500/30"
          >
            {item}
            <button
              type="button"
              onClick={() => remove(i)}
              className="p-0.5 rounded hover:bg-cyan-500/30 text-cyan-300"
              aria-label={`Удалить ${item}`}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </span>
        ))}
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={addCurrent}
          placeholder={placeholder}
          className="flex-1 min-w-[120px] bg-transparent border-none outline-none text-white text-sm placeholder:text-zinc-500"
        />
      </div>
    </div>
  );
};

type EditStoryDrawerProps = {
  story: Story | null;
  onClose: () => void;
  onSaved: () => void;
};

export function EditStoryDrawer({ story, onClose, onSaved }: EditStoryDrawerProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [isPremium, setIsPremium] = useState(false);
  const [genres, setGenres] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [coverImageUrl, setCoverImageUrl] = useState('');
  const [audioUrl, setAudioUrl] = useState('');
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!story) return;
    setTitle(story.title);
    setDescription(story.description ?? '');
    setContent(story.content ?? '');
    setIsPremium(story.isPremium);
    setGenres(story.genres ?? []);
    setTags(story.tags ?? []);
    setCoverImageUrl(story.coverImage ?? '');
    setAudioUrl(story.audioSrc ?? '');
    setCoverFile(null);
    setAudioFile(null);
  }, [story]);

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };

  const handleSave = async () => {
    if (!story) return;
    if (!title.trim()) {
      toast.error('Введите название');
      return;
    }
    setSaving(true);
    try {
      let imageUrl = coverImageUrl;
      let finalAudioUrl = audioUrl;

      if (coverFile) {
        const presign = await getPresignedUrl(coverFile.name, coverFile.type);
        if ('error' in presign) {
          toast.error(presign.error);
          setSaving(false);
          return;
        }
        const res = await fetch(presign.uploadUrl, {
          method: 'PUT',
          body: coverFile,
          headers: { 'Content-Type': coverFile.type },
        });
        if (!res.ok) {
          toast.error('Ошибка загрузки обложки в R2');
          setSaving(false);
          return;
        }
        imageUrl = presign.publicUrl;
      }

      if (audioFile) {
        const presign = await getPresignedUrl(audioFile.name, audioFile.type);
        if ('error' in presign) {
          toast.error(presign.error);
          setSaving(false);
          return;
        }
        const res = await fetch(presign.uploadUrl, {
          method: 'PUT',
          body: audioFile,
          headers: { 'Content-Type': audioFile.type },
        });
        if (!res.ok) {
          toast.error('Ошибка загрузки аудио в R2');
          setSaving(false);
          return;
        }
        finalAudioUrl = presign.publicUrl;
      }

      const payload: Parameters<typeof updateStory>[1] = {
        title: title.trim(),
        description: description.trim() || undefined,
        content: content.trim() || undefined,
        is_premium: isPremium,
        genres: genres.length ? genres : undefined,
        tags: tags.length ? tags : undefined,
      };
      if (imageUrl) payload.image_url = imageUrl;
      if (finalAudioUrl) payload.audio_url = finalAudioUrl;

      const result = await updateStory(story.id, payload);
      if (result.success) {
        toast.success('Изменения сохранены');
        onClose();
        onSaved();
      } else {
        toast.error(result.error);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Ошибка сохранения');
    } finally {
      setSaving(false);
    }
  };

  if (!story) return null;

  const [coverPreviewBlobUrl, setCoverPreviewBlobUrl] = useState('');
  useEffect(() => {
    if (!coverFile) {
      setCoverPreviewBlobUrl('');
      return;
    }
    const u = URL.createObjectURL(coverFile);
    setCoverPreviewBlobUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [coverFile]);
  const coverPreviewDisplay = coverFile ? coverPreviewBlobUrl : coverImageUrl;
  let audioLabel = '—';
  if (audioFile) audioLabel = audioFile.name;
  else if (audioUrl) {
    try {
      audioLabel = new URL(audioUrl).pathname.split('/').pop() ?? 'Аудио';
    } catch {
      audioLabel = 'Аудио';
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-labelledby="edit-story-title"
    >
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleOverlayClick}
        aria-hidden="true"
      />
      <div className="relative w-full max-w-2xl bg-zinc-900 border-l border-zinc-800 shadow-2xl overflow-y-auto flex flex-col max-h-full">
        <div className="sticky top-0 z-10 flex items-center justify-between p-4 border-b border-zinc-800 bg-zinc-900">
          <h2 id="edit-story-title" className="text-lg font-semibold text-white">
            Редактировать: {story.title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-lg text-zinc-500 hover:text-white hover:bg-zinc-800 transition-colors"
            aria-label="Закрыть"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-1">Название</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-zinc-700 bg-zinc-800 text-white focus:border-cyan-600 focus:outline-none text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-1">Описание</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-lg border border-zinc-700 bg-zinc-800 text-white focus:border-cyan-600 focus:outline-none text-sm resize-y"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-1">Текст рассказа</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={12}
              className="w-full px-3 py-2 rounded-lg border border-zinc-700 bg-zinc-800 text-white focus:border-cyan-600 focus:outline-none text-sm resize-y font-mono text-sm"
              placeholder="Полный текст истории (если хранится в БД)"
            />
          </div>

          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-zinc-400">Доступ</span>
            <button
              type="button"
              role="switch"
              aria-checked={isPremium}
              onClick={() => setIsPremium((v) => !v)}
              className={`relative inline-flex h-6 w-11 shrink-0 rounded-full border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 ${
                isPremium ? 'bg-amber-500 border-amber-500' : 'bg-zinc-700 border-zinc-600'
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow ring-0 transition-transform ${
                  isPremium ? 'translate-x-5' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span className="text-sm text-zinc-400">{isPremium ? 'Premium' : 'Free'}</span>
          </div>

          <ChipInput
            label="Жанры"
            value={genres}
            onChange={setGenres}
            placeholder="Жанр + Enter"
          />
          <ChipInput
            label="Теги"
            value={tags}
            onChange={setTags}
            placeholder="Тег + Enter"
          />

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-1">Обложка</label>
            <div className="flex flex-wrap items-start gap-4">
              <div className="relative w-24 h-24 rounded-lg overflow-hidden bg-zinc-800 border border-zinc-700">
                {coverPreviewDisplay ? (
                  <Image
                    src={coverPreviewDisplay}
                    alt=""
                    fill
                    className="object-cover"
                    unoptimized
                    sizes="96px"
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-zinc-500">
                    <ImageIcon className="w-8 h-8" />
                  </div>
                )}
              </div>
              <div>
                <label className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-zinc-300 hover:bg-zinc-700 cursor-pointer">
                  <Upload className="w-4 h-4" />
                  Загрузить новую обложку
                  <input
                    type="file"
                    accept="image/*"
                    className="sr-only"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) setCoverFile(f);
                    }}
                  />
                </label>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-1">Аудио</label>
            <div className="flex flex-wrap items-center gap-4">
              {audioUrl && !audioFile && (
                <audio controls src={audioUrl} className="max-w-full h-9 rounded bg-zinc-800">
                  Текущее аудио
                </audio>
              )}
              <span className="text-sm text-zinc-500 truncate max-w-[200px]" title={audioLabel}>
                {audioLabel}
              </span>
              <label className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-zinc-300 hover:bg-zinc-700 cursor-pointer">
                <Music className="w-4 h-4" />
                Загрузить новое аудио
                <input
                  type="file"
                  accept="audio/mpeg,audio/wav,audio/*,.mp3,.wav"
                  className="sr-only"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setAudioFile(f);
                  }}
                />
              </label>
            </div>
          </div>

          <div className="flex gap-3 pt-4">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              Сохранить изменения
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg border border-zinc-600 text-zinc-400 text-sm hover:bg-zinc-800"
            >
              Отмена
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
