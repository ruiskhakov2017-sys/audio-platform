'use client';

import { motion } from 'framer-motion';
import Link from 'next/link';

export function FooterCTA() {
  return (
    <section className="py-24 px-6 relative overflow-hidden bg-black border-t border-white/5">
      <div className="max-w-4xl mx-auto text-center relative z-10">
        <motion.h2
          className="font-heading text-3xl md:text-5xl font-bold text-white mb-6"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
        >
          Готов(а) воплотить свои фантазии?
        </motion.h2>
        <motion.p
          className="text-lg md:text-xl text-zinc-400 mb-10 max-w-2xl mx-auto"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.1 }}
        >
          Открой полный доступ к эксклюзивной библиотеке аудиорассказов.
        </motion.p>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.2 }}
        >
          <Link
            href="/premium"
            className="inline-flex items-center justify-center px-8 py-4 text-lg font-bold text-zinc-950 bg-gradient-to-r from-amber-500 to-yellow-500 rounded-full hover:from-amber-400 hover:to-yellow-400 transition-all shadow-[0_0_20px_rgba(245,158,11,0.4)] hover:shadow-[0_0_30px_rgba(245,158,11,0.6)]"
          >
            Оформить Премиум
          </Link>
        </motion.div>
      </div>
      
      {/* Background glow effect */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-[#FFD700]/5 rounded-full blur-[100px] pointer-events-none" />
    </section>
  );
}
