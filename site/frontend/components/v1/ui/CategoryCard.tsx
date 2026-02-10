'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

interface CategoryCardProps {
  title: string;
  description: string;
  href: string;
  icon: React.ReactNode;
}

export default function CategoryCard({ title, description, href, icon }: CategoryCardProps) {
  return (
    <Link
      href={href}
      className="group flex flex-col rounded-2xl p-6 transition-all duration-300 hover:scale-[1.02]"
      style={{
        backgroundColor: 'rgba(30, 58, 95, 0.4)',
        border: '1px solid rgba(59, 130, 246, 0.2)',
        textDecoration: 'none',
      }}
    >
      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl text-white"
        style={{ backgroundColor: '#2563eb' }}
      >
        {icon}
      </div>
      <h3 className="text-lg font-bold" style={{ color: '#f1f5f9' }}>
        {title}
      </h3>
      <p className="mt-2 flex-1 text-sm leading-relaxed" style={{ color: '#94a3b8' }}>
        {description}
      </p>
      <span
        className="mt-4 inline-flex items-center gap-1 text-sm font-medium"
        style={{ color: '#93c5fd' }}
      >
        Перейти
        <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
