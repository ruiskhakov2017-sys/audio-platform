'use client';

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[App Error]', error);
  }, [error]);

  return (
    <div
      style={{
        padding: 24,
        background: '#000814',
        color: '#fff',
        minHeight: '100vh',
        fontFamily: 'system-ui, sans-serif',
        fontSize: 16,
      }}
    >
      <h1 style={{ marginBottom: 12, fontSize: 20 }}>Что-то пошло не так</h1>
      <p style={{ color: '#94a3b8', marginBottom: 16, wordBreak: 'break-word' }}>
        {error.message || 'Неизвестная ошибка'}
      </p>
      <button
        type="button"
        onClick={reset}
        style={{
          padding: '10px 20px',
          background: '#00B4D8',
          color: '#fff',
          border: 'none',
          borderRadius: 8,
          cursor: 'pointer',
          fontSize: 14,
        }}
      >
        Попробовать снова
      </button>
    </div>
  );
}
