'use client';

import { useEffect } from 'react';
import { useAuthStore } from '@/store/authStore';

export function AuthToast() {
  const toastMessage = useAuthStore((s) => s.toastMessage);
  const clearToast = useAuthStore((s) => s.clearToast);

  useEffect(() => {
    if (!toastMessage) return;
    const t = setTimeout(clearToast, 3000);
    return () => clearTimeout(t);
  }, [toastMessage, clearToast]);

  if (!toastMessage) return null;

  return (
    <div
      className="fixed bottom-28 left-1/2 z-[60] -translate-x-1/2 rounded-full bg-zinc-800/95 px-6 py-3 text-white shadow-lg backdrop-blur-sm border border-white/10"
      role="status"
      aria-live="polite"
    >
      {toastMessage}
    </div>
  );
}
