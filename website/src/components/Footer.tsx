import { Bot, GitBranch, Globe, Terminal } from 'lucide-react';

export const Footer = () => {
  return (
    <footer className="py-12 px-4 border-t border-white/5 bg-[#050510]">
      <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-cyber-blue/10 border border-cyber-blue/20">
            <Bot size={24} className="text-cyber-blue" />
          </div>
          <span className="text-xl font-black tracking-tighter">
            Tieba<span className="text-cyber-blue">Mecha</span>
          </span>
        </div>

        <div className="flex gap-8 text-gray-500 text-sm font-medium">
          <a href="https://github.com/hwdemtv/TiebaMecha/releases" target="_blank" rel="noopener noreferrer" className="hover:text-cyber-blue transition-colors">产品路线图</a>
          <a href="https://github.com/hwdemtv/TiebaMecha#readme" target="_blank" rel="noopener noreferrer" className="hover:text-cyber-blue transition-colors">用户手册</a>
          <a href="https://github.com/hwdemtv/TiebaMecha/issues" target="_blank" rel="noopener noreferrer" className="hover:text-cyber-blue transition-colors">授权激活</a>
          <a href="https://github.com/hwdemtv" target="_blank" rel="noopener noreferrer" className="hover:text-cyber-blue transition-colors">关于作者</a>
        </div>

        <div className="flex gap-4">
          <a href="https://github.com/hwdemtv/TiebaMecha" target="_blank" rel="noopener noreferrer" className="p-2 rounded-full bg-white/5 hover:bg-white/10 transition-colors">
            <GitBranch size={20} />
          </a>
          <a href="https://pypi.org/project/tieba-mecha/" target="_blank" rel="noopener noreferrer" className="p-2 rounded-full bg-white/5 hover:bg-white/10 transition-colors">
            <Terminal size={20} />
          </a>
          <a href="https://github.com/hwdemtv" target="_blank" rel="noopener noreferrer" className="p-2 rounded-full bg-white/5 hover:bg-white/10 transition-colors">
            <Globe size={20} />
          </a>
        </div>
      </div>
      <div className="max-w-6xl mx-auto mt-12 text-center text-gray-600 text-[10px] tracking-[0.3em] font-mono uppercase">
        © 2026 TiebaMecha Project. Built for Safety, Speed, and Stability.
      </div>
    </footer>
  );
};
