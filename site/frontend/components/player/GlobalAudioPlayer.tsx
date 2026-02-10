'use client';

import useAudioEngine from '@/lib/useAudioEngine';

/**
 * Глобальный аудио-движок без UI. Монтируется один раз в layout.
 * Подписан на usePlayerStore: при смене currentTrack загружает новый src,
 * при isPlaying — play/pause. Обновляет position/duration в сторе.
 * Обеспечивает непрерывное воспроизведение при навигации.
 */
export function GlobalAudioPlayer() {
  useAudioEngine();
  return null;
}
