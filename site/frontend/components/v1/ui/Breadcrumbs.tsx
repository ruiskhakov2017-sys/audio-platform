'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

type BreadcrumbsProps = {
  items: BreadcrumbItem[];
};

export default function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav aria-label="Хлебные крошки" className="flex flex-wrap items-center gap-1 text-sm">
      {items.map((item, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && (
            <ChevronRight className="h-4 w-4 shrink-0" style={{ color: '#64748b' }} />
          )}
          {item.href ? (
            <Link
              href={item.href}
              className="transition-colors hover:opacity-90"
              style={{ color: '#93c5fd', textDecoration: 'none' }}
            >
              {item.label}
            </Link>
          ) : (
            <span style={{ color: '#f1f5f9' }}>{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
