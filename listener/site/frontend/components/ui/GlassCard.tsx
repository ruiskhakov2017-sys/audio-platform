'use client';

import { type HTMLAttributes, type ReactNode } from 'react';

type GlassCardProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  className?: string;
};

export default function GlassCard({ children, className = '', ...props }: GlassCardProps) {
  return (
    <div
      className={`rounded-[2.5rem] border border-white/10 bg-[#001d3d]/40 p-6 backdrop-blur-xl ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
