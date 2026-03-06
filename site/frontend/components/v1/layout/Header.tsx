'use client';

import Link from 'next/link';
import Image from 'next/image';

export default function Header() {
  return (
    <header className="sticky top-0 z-40 w-full border-b border-white/10 bg-[#050505]/80 backdrop-blur">
      <div className="mx-auto max-w-6xl px-4 md:px-8">
        <div className="flex h-14 items-center justify-between">
          <Link href="/" className="text-sm font-semibold text-white">
            Dirty Secrets
          </Link>
          <div className="flex items-center gap-3">
            <input
              placeholder="Search"
              className="h-9 w-44 rounded-full border border-white/10 bg-white/5 px-3 text-sm text-white/80 outline-none placeholder:text-white/40"
            />
            <div className="relative h-9 w-9 overflow-hidden rounded-full border border-white/15">
              <Image
                src="https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=200&auto=format&fit=crop"
                alt="User"
                fill
                sizes="36px"
                className="object-cover"
              />
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
