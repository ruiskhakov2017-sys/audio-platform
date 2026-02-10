'use client';

import { type ButtonHTMLAttributes, type ReactNode } from 'react';

type Variant = 'primary' | 'glass';

type NeonButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  children: ReactNode;
  className?: string;
  asChild?: boolean;
};

export default function NeonButton({
  variant = 'primary',
  children,
  className = '',
  asChild,
  ...props
}: NeonButtonProps) {
  const base = 'inline-flex items-center justify-center rounded-full px-8 py-3.5 text-sm font-bold transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#00B4D8]/50 disabled:opacity-50';
  const variants = {
    primary:
      'border-2 border-[#00B4D8] bg-[#00B4D8]/15 text-[#7dd3fc] shadow-[0_0_24px_rgba(0,180,216,0.25)] hover:bg-[#00B4D8]/25 hover:shadow-[0_0_36px_rgba(0,180,216,0.4)] hover:border-[#7dd3fc]/60',
    glass:
      'border border-white/15 bg-white/5 text-white backdrop-blur-xl hover:border-white/25 hover:bg-white/10 hover:text-[#7dd3fc]',
  };

  if (asChild) {
    return (
      <span className={`${base} ${variants[variant]} ${className}`}>
        {children}
      </span>
    );
  }

  return (
    <button
      type="button"
      className={`${base} ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
