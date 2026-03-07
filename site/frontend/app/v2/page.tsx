'use client';

import { useEffect, useState, useRef } from 'react';

// Описание структуры данных (что нам присылает бэкенд)
interface Story {
  id: number;
  title: string;
  cover_image: string | null;
  audio_file: string | null;
  author: {
    name: string;
  };
  genre: {
    name: string;
  };
}

export default function Home() {
  const [stories, setStories] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeStory, setActiveStory] = useState<Story | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
  const storiesUrl = apiBase ? `${apiBase.replace(/\/$/, '')}/api/stories/` : '';

  // 1. Загрузка рассказов при старте
  useEffect(() => {
    if (!storiesUrl) {
      setLoading(false);
      return;
    }
    fetch(storiesUrl)
      .then((res) => res.json())
      .then((data) => {
        setStories(data);
        setLoading(false);
      })
      .catch((err) => console.error(err));
  }, [storiesUrl]);

  // 2. Автозапуск плеера при выборе рассказа
  useEffect(() => {
    if (activeStory && audioRef.current) {
      audioRef.current.load();
      audioRef.current.play().catch(e => console.log("Автозапуск не сработал:", e));
    }
  }, [activeStory]);

  return (
    <main className="min-h-screen bg-gradient-to-b from-gray-900 to-black text-white p-8 pb-32">
      {/* Шапка сайта */}
      <header className="mb-12 text-center">
        <h1 className="text-5xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-600 mb-4">
          Audio Stories
        </h1>
        <p className="text-gray-400">Твоя личная библиотека историй</p>
      </header>

      {/* Индикатор загрузки */}
      {loading && <div className="text-center animate-pulse text-xl">Загрузка библиотеки...</div>}

      {/* Сетка с карточками */}
      <div className="flex flex-wrap justify-center gap-8 max-w-7xl mx-auto">
        {stories.map((story) => (
          <div
            key={story.id}
            onClick={() => setActiveStory(story)}
            className={`w-full max-w-xs group relative bg-gray-800 rounded-xl overflow-hidden shadow-lg cursor-pointer transition-transform hover:-translate-y-2 ${activeStory?.id === story.id ? 'ring-2 ring-blue-500' : ''}`}
          >
            {/* Картинка (обложка) */}
            <div className="h-64 w-full relative bg-gray-700">
              {story.cover_image ? (
                <img
                  src={story.cover_image}
                  alt={story.title}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500">Нет обложки</div>
              )}

              {/* Иконка Play при наведении */}
              <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                <div className="w-14 h-14 bg-blue-600 rounded-full flex items-center justify-center shadow-lg">
                  <svg className="w-6 h-6 text-white ml-1" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                </div>
              </div>
            </div>

            {/* Текст под картинкой */}
            <div className="p-4">
              <h3 className="font-bold truncate text-lg">{story.title}</h3>
              <div className="flex justify-between items-center mt-2">
                <p className="text-sm text-gray-400">{story.author?.name}</p>
                <span className="text-xs px-2 py-1 bg-gray-700 rounded text-gray-300">{story.genre?.name}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Плеер (выезжает снизу) */}
      {activeStory && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900/95 border-t border-gray-800 p-4 backdrop-blur-lg animate-slide-up z-50">
          <div className="max-w-4xl mx-auto flex items-center gap-4">
            {/* Мини-обложка в плеере */}
            {activeStory.cover_image && (
              <img src={activeStory.cover_image} className="w-14 h-14 rounded bg-gray-800 object-cover hidden sm:block shadow-md" />
            )}

            {/* Название в плеере */}
            <div className="flex-1 min-w-0">
              <h4 className="font-bold truncate text-base text-blue-400">{activeStory.title}</h4>
              <p className="text-xs text-gray-400">{activeStory.author?.name}</p>
            </div>

            {/* Сам плеер */}
            <audio
              ref={audioRef}
              controls
              className="w-full max-w-xs h-8 opacity-90 hover:opacity-100 transition-opacity"
              style={{ filter: "invert(1) hue-rotate(180deg)" }}
            >
              <source src={activeStory.audio_file || ''} type="audio/mpeg" />
            </audio>
          </div>
        </div>
      )}
    </main>
  );
}
