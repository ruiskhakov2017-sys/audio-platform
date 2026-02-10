import React from 'react';

export const AudioWave = ({ className = "" }: { className?: string }) => {
  return (
    <div className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}>
      <svg
        className="absolute left-0 top-1/2 h-[500px] w-full -translate-y-1/2 opacity-30 mix-blend-screen"
        viewBox="0 0 1440 320"
        xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="none"
      >
        <path
          fill="none"
          stroke="url(#grad1)"
          strokeWidth="2"
          d="M0,160L48,144C96,128,192,96,288,106.7C384,117,480,171,576,181.3C672,192,768,160,864,144C960,128,1056,128,1152,138.7C1248,149,1344,171,1392,181.3L1440,192"
        />
        <path
          fill="none"
          stroke="url(#grad2)"
          strokeWidth="2"
          d="M0,160L48,170.7C96,181,192,203,288,197.3C384,192,480,160,576,149.3C672,139,768,149,864,165.3C960,181,1056,203,1152,197.3C1248,192,1344,160,1392,144L1440,136"
        />
         <path
          fill="none"
          stroke="url(#grad1)"
          strokeWidth="1"
          d="M0,160L40,154.7C80,149,160,139,240,144C320,149,400,171,480,186.7C560,203,640,213,720,202.7C800,192,880,160,960,144C1040,128,1120,128,1200,138.7C1280,149,1360,171,1400,181.3L1440,192"
          className="opacity-50"
        />
        <defs>
          <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" style={{ stopColor: '#a855f7', stopOpacity: 0 }} />
            <stop offset="50%" style={{ stopColor: '#d8b4fe', stopOpacity: 1 }} />
            <stop offset="100%" style={{ stopColor: '#a855f7', stopOpacity: 0 }} />
          </linearGradient>
          <linearGradient id="grad2" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" style={{ stopColor: '#ec4899', stopOpacity: 0 }} />
            <stop offset="50%" style={{ stopColor: '#fbcfe8', stopOpacity: 1 }} />
            <stop offset="100%" style={{ stopColor: '#ec4899', stopOpacity: 0 }} />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
};
