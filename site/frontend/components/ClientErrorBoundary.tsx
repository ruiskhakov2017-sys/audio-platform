'use client';

import React from 'react';

type Props = { children: React.ReactNode };
type State = { hasError: boolean; error: Error | null };

export class ClientErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError && this.state.error) {
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
            {this.state.error.message || 'Неизвестная ошибка'}
          </p>
          <button
            type="button"
            onClick={() => this.setState({ hasError: false, error: null })}
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
    return this.props.children;
  }
}
