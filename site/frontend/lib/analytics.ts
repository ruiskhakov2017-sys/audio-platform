/**
 * Отправка событий в Яндекс.Метрику и Google Analytics 4.
 * Если в .env нет NEXT_PUBLIC_YM_ID / NEXT_PUBLIC_GA_MEASUREMENT_ID — функции ничего не делают.
 */

declare global {
  interface Window {
    ym?: (id: number, method: string, ...args: unknown[]) => void;
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

const ymId = () => {
  const n = Number(process.env.NEXT_PUBLIC_YM_ID);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

const gaId = () => process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim() || '';

function ymReachGoal(goal: string, params?: Record<string, unknown>) {
  if (typeof window === 'undefined') return;
  const id = ymId();
  if (!id || typeof window.ym !== 'function') return;
  try {
    if (params && Object.keys(params).length) {
      window.ym(id, 'reachGoal', goal, params);
    } else {
      window.ym(id, 'reachGoal', goal);
    }
  } catch {
    /* ignore */
  }
}

function gaEvent(name: string, params?: Record<string, unknown>) {
  if (typeof window === 'undefined') return;
  const id = gaId();
  if (!id || typeof window.gtag !== 'function') return;
  try {
    window.gtag('event', name, params ?? {});
  } catch {
    /* ignore */
  }
}

export function trackAudioPlayStart(payload: {
  storyId: string | number;
  title: string;
  isPremium: boolean;
  durationSec?: number;
}) {
  const p = {
    story_id: String(payload.storyId),
    title: payload.title.slice(0, 120),
    is_premium: payload.isPremium,
    duration_sec: payload.durationSec ?? undefined,
  };
  ymReachGoal('audio_play_start', p);
  gaEvent('audio_play_start', {
    story_id: p.story_id,
    is_premium: p.is_premium,
    value: p.duration_sec,
  });
}

export function trackAudioMilestone(payload: {
  storyId: string | number;
  percent: 25 | 50 | 75 | 100;
  positionSec: number;
  durationSec: number;
}) {
  const p = {
    story_id: String(payload.storyId),
    percent: payload.percent,
    position_sec: Math.floor(payload.positionSec),
    duration_sec: Math.floor(payload.durationSec),
  };
  ymReachGoal('audio_progress', p);
  gaEvent('audio_progress', {
    story_id: p.story_id,
    percent: p.percent,
  });
}

export function trackAudioComplete(payload: { storyId: string | number; title: string }) {
  const p = {
    story_id: String(payload.storyId),
    title: payload.title.slice(0, 120),
  };
  ymReachGoal('audio_complete', p);
  gaEvent('audio_complete', { story_id: p.story_id });
}
