'use client';

import { useState, useCallback } from 'react';
import Image from 'next/image';
import { Music, ImageIcon } from 'lucide-react';
import { getPresignedUrl, saveStoryToSupabase } from '@/app/actions/upload';

export default function AdminUploadPage() {
  const [title, setTitle] = useState('');
  const [genresInput, setGenresInput] = useState('');
  const [tags, setTags] = useState('');
  const [description, setDescription] = useState('');
  const [isPremium, setIsPremium] = useState(false);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioName, setAudioName] = useState<string | null>(null);
  const [dragCover, setDragCover] = useState(false);
  const [dragAudio, setDragAudio] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleCoverDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragCover(false);
    const file = e.dataTransfer.files[0];
    if (file?.type.startsWith('image/')) {
      setCoverFile(file);
      setCoverPreview(URL.createObjectURL(file));
    }
  }, []);

  const handleAudioDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragAudio(false);
    const file = e.dataTransfer.files[0];
    if (file && (file.type === 'audio/mpeg' || file.type === 'audio/wav' || /\.(mp3|wav)$/i.test(file.name))) {
      setAudioFile(file);
      setAudioName(file.name);
    }
  }, []);

  const resetForm = () => {
    setTitle('');
    setGenresInput('');
    setTags('');
    setDescription('');
    setIsPremium(false);
    setCoverFile(null);
    setCoverPreview(null);
    setAudioFile(null);
    setAudioName(null);
    if (coverPreview) URL.revokeObjectURL(coverPreview);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);
    if (!coverFile || !audioFile) {
      setMessage({ type: 'error', text: 'Выберите обложку и аудиофайл.' });
      return;
    }
    setSubmitting(true);
    try {
      const [imagePresign, audioPresign] = await Promise.all([
        getPresignedUrl(coverFile.name, coverFile.type),
        getPresignedUrl(audioFile.name, audioFile.type),
      ]);
      if ('error' in imagePresign) {
        setMessage({ type: 'error', text: imagePresign.error });
        setSubmitting(false);
        return;
      }
      if ('error' in audioPresign) {
        setMessage({ type: 'error', text: audioPresign.error });
        setSubmitting(false);
        return;
      }
      const [imageUploadRes, audioUploadRes] = await Promise.all([
        fetch(imagePresign.uploadUrl, {
          method: 'PUT',
          body: coverFile,
          headers: { 'Content-Type': coverFile.type },
        }),
        fetch(audioPresign.uploadUrl, {
          method: 'PUT',
          body: audioFile,
          headers: { 'Content-Type': audioFile.type },
        }),
      ]);
      if (!imageUploadRes.ok) {
        setMessage({ type: 'error', text: 'Ошибка загрузки обложки в R2.' });
        setSubmitting(false);
        return;
      }
      if (!audioUploadRes.ok) {
        setMessage({ type: 'error', text: 'Ошибка загрузки аудио в R2.' });
        setSubmitting(false);
        return;
      }
      const genresList = genresInput.split(',').map((t) => t.trim()).filter(Boolean);
      const tagsList = tags.split(',').map((t) => t.trim()).filter(Boolean);
      if (genresList.length === 0) {
        setMessage({ type: 'error', text: 'Укажите хотя бы один жанр (через запятую).' });
        setSubmitting(false);
        return;
      }
      const saveResult = await saveStoryToSupabase({
        title,
        genres: genresList,
        image_url: imagePresign.publicUrl,
        audio_url: audioPresign.publicUrl,
        duration: 0,
        is_premium: isPremium,
        ...(description && { description }),
        ...(tagsList.length ? { tags: tagsList } : {}),
      });
      if (!saveResult.success) {
        setMessage({ type: 'error', text: saveResult.error });
        setSubmitting(false);
        return;
      }
      setMessage({ type: 'success', text: 'Успешно! История сохранена в базу.' });
      resetForm();
    } catch (err) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Неизвестная ошибка',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold text-white mb-8">Загрузка истории</h1>

      {message && (
        <p
          className={`mb-6 text-sm ${message.type === 'success' ? 'text-emerald-400' : 'text-rose-400'}`}
        >
          {message.text}
        </p>
      )}

      <form onSubmit={handleSubmit} className="max-w-3xl space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Обложка</label>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragCover(true); }}
              onDragLeave={() => setDragCover(false)}
              onDrop={handleCoverDrop}
              className={`aspect-square max-w-[280px] rounded-xl border-2 border-dashed flex flex-col items-center justify-center text-center transition-colors ${
                dragCover ? 'border-cyan-500 bg-cyan-500/10' : 'border-zinc-700 bg-zinc-800/50'
              }`}
            >
              {coverPreview ? (
                <div className="relative w-full h-full rounded-xl overflow-hidden">
                  <Image src={coverPreview} alt="Preview" fill className="object-cover" unoptimized />
                </div>
              ) : (
                <>
                  <ImageIcon className="w-12 h-12 text-zinc-500 mb-2" />
                  <p className="text-sm text-zinc-500 px-4">Перетащите обложку сюда или выберите файл</p>
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    id="cover-upload"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        setCoverFile(file);
                        setCoverPreview(URL.createObjectURL(file));
                      }
                    }}
                  />
                  <label htmlFor="cover-upload" className="mt-2 text-sm text-cyan-500 cursor-pointer hover:underline">
                    Выбрать файл
                  </label>
                </>
              )}
            </div>
            {coverPreview && (
              <>
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  id="cover-upload-2"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setCoverFile(file);
                      setCoverPreview(URL.createObjectURL(file));
                    }
                  }}
                />
                <label htmlFor="cover-upload-2" className="mt-2 inline-block text-sm text-cyan-500 cursor-pointer hover:underline">
                  Заменить обложку
                </label>
              </>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Аудио (MP3 / WAV)</label>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragAudio(true); }}
              onDragLeave={() => setDragAudio(false)}
              onDrop={handleAudioDrop}
              className={`min-h-[200px] rounded-xl border-2 border-dashed flex flex-col items-center justify-center text-center transition-colors ${
                dragAudio ? 'border-cyan-500 bg-cyan-500/10' : 'border-zinc-700 bg-zinc-800/50'
              }`}
            >
              {audioName ? (
                <p className="text-white font-medium">{audioName}</p>
              ) : (
                <>
                  <Music className="w-12 h-12 text-zinc-500 mb-2" />
                  <p className="text-sm text-zinc-500 px-4">Перетащите MP3 сюда или выберите файл</p>
                </>
              )}
              <input
                type="file"
                accept="audio/mpeg,audio/wav,.mp3,.wav"
                className="hidden"
                id="audio-upload"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    setAudioFile(file);
                    setAudioName(file.name);
                  }
                }}
              />
              <label htmlFor="audio-upload" className="mt-2 text-sm text-cyan-500 cursor-pointer hover:underline">
                Выбрать файл
              </label>
            </div>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-1.5">Название</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none"
            placeholder="Название рассказа"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-1.5">Описание</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            rows={4}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none resize-y"
            placeholder="Описание истории"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-1.5">Жанры (через запятую, несколько)</label>
          <input
            type="text"
            value={genresInput}
            onChange={(e) => setGenresInput(e.target.value)}
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none"
            placeholder="романтика, 18+, по принуждению"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-zinc-400 mb-1.5">Теги (через запятую)</label>
          <input
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none"
            placeholder="romance, asmr, night"
          />
        </div>

        <div className="flex items-center gap-3">
          <span className="text-sm text-zinc-400">Premium Content</span>
          <button
            type="button"
            role="switch"
            aria-checked={isPremium}
            onClick={() => setIsPremium(!isPremium)}
            className={`relative w-11 h-6 rounded-full transition-colors ${isPremium ? 'bg-cyan-600' : 'bg-zinc-700'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${isPremium ? 'translate-x-5' : 'translate-x-0'}`}
            />
          </button>
          <span className="text-sm text-zinc-500">{isPremium ? 'Платный контент' : 'Бесплатный'}</span>
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="w-full py-3 rounded-lg bg-cyan-600 text-white font-medium hover:bg-cyan-500 transition-colors disabled:opacity-50"
        >
          {submitting ? 'Загрузка...' : 'Опубликовать'}
        </button>
      </form>
    </div>
  );
}
