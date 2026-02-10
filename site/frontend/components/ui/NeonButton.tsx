'use client';

import { motion } from 'framer-motion';
import Link from 'next/link';
import { ReactNode } from 'react';

interface NeonButtonProps {
    children: ReactNode;
    variant?: 'primary' | 'glass';
    size?: 'default' | 'large';
    onClick?: () => void;
    href?: string;
    target?: string;
    className?: string;
}

export function NeonButton({
    children,
    variant = 'primary',
    size = 'default',
    onClick,
    href,
    target,
    className = ''
}: NeonButtonProps) {
    const sizeClasses = size === 'large' ? 'py-4 px-10 text-lg font-bold' : 'px-8 py-3 text-sm font-medium';
    const baseClasses = `rounded-full transition-all duration-300 ${sizeClasses}`;

    const variantClasses = {
        primary: `relative overflow-hidden bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-[0_0_20px_rgba(6,182,212,0.5)] hover:shadow-[0_0_30px_rgba(6,182,212,0.8)] group ${size === 'large' ? 'shadow-lg shadow-cyan-500/20' : ''}`,
        glass: 'glass-premium text-white hover:border-cyan-400/50 hover:shadow-[0_0_20px_rgba(6,182,212,0.3)]'
    };

    const finalClasses = `${baseClasses} ${variantClasses[variant]} ${className}`;

    if (href) {
        const isExternal = href.startsWith('http');
        const button = (
                <motion.button
                    className={finalClasses}
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.95 }}
                >
                    <span className="relative z-10">{children}</span>
                    {variant === 'primary' && (
                        <div className="absolute inset-0 -translate-x-full group-hover:translate-x-full transition-transform duration-700 bg-gradient-to-r from-transparent via-white/20 to-transparent skew-x-12" />
                    )}
                </motion.button>
        );
        return isExternal ? (
            <a href={href} target={target ?? '_blank'} rel="noopener noreferrer">
                {button}
            </a>
        ) : (
            <Link href={href}>{button}</Link>
        );
    }

    return (
        <motion.button
            className={finalClasses}
            onClick={onClick}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
        >
            <span className="relative z-10">{children}</span>
            {variant === 'primary' && (
                <div className="absolute inset-0 -translate-x-full group-hover:translate-x-full transition-transform duration-700 bg-gradient-to-r from-transparent via-white/20 to-transparent skew-x-12" />
            )}
        </motion.button>
    );
}
