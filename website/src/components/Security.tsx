import { motion } from 'framer-motion';
import { Activity, Shield, MousePointer2 } from 'lucide-react';

export const Security = () => {
  return (
    <section className="py-24 px-4 relative overflow-hidden bg-[#0a0a20]">
      <div className="absolute inset-0 opacity-10">
        <div className="grid grid-cols-[repeat(50,minmax(0,1fr))] h-full w-full">
          {Array.from({ length: 2500 }).map((_, i) => (
            <div key={i} className="border-[0.5px] border-white/10" />
          ))}
        </div>
      </div>
      
      <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center gap-16 relative">
        <motion.div 
          className="flex-1"
          initial={{ opacity: 0, x: -50 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={{ once: true }}
        >
          <div className="inline-block p-2 px-4 rounded-full bg-cyber-pink/10 border border-cyber-pink/20 text-cyber-pink text-sm mb-6">
            Bionic Anti-Detection
          </div>
          <h2 className="text-4xl md:text-5xl font-black mb-8 leading-tight">
            全方位的<span className="text-cyber-blue">拟人化</span><br/>安全增强方案
          </h2>
          
          <div className="space-y-8">
            <div className="flex gap-4">
              <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-cyber-blue/10 flex items-center justify-center border border-cyber-blue/20">
                <Activity className="text-cyber-blue" />
              </div>
              <div>
                <h3 className="text-xl font-bold mb-2">高斯分布延迟调度</h3>
                <p className="text-gray-400 text-sm">发帖延迟遵循正态演化，拒绝固定间隔，彻底告别机械特征被识别。即便在大规模矩阵作业下依然保持人类般的波动。 </p>
              </div>
            </div>

            <div className="flex gap-4">
              <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-cyber-purple/10 flex items-center justify-center border border-cyber-purple/20">
                <MousePointer2 className="text-cyber-purple" />
              </div>
              <div>
                <h3 className="text-xl font-bold mb-2">零宽字符混淆机制</h3>
                <p className="text-gray-400 text-sm">在不影响阅读的前提下，自动在标题和正文内注入可见或不可见的零宽字符，绕开平台的内容指纹库审核。</p>
              </div>
            </div>

            <div className="flex gap-4">
              <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-cyber-pink/10 flex items-center justify-center border border-cyber-pink/20">
                <Shield className="text-cyber-pink" />
              </div>
              <div>
                <h3 className="text-xl font-bold mb-2">生理节律权重调整</h3>
                <p className="text-gray-400 text-sm">系统自动基于现实作息时间（如深夜 1-7 点）优化执行频率，让账号行为更具生理连续性与合规性。</p>
              </div>
            </div>
          </div>
        </motion.div>

        <motion.div 
          className="flex-1 w-full"
          initial={{ opacity: 0, scale: 0.9 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
        >
          <div className="relative glass-card border-cyber-blue/30 overflow-hidden shadow-2xl shadow-cyber-blue/10">
            <div className="flex items-center gap-2 mb-4 text-xs font-mono text-gray-500 border-b border-white/10 pb-4">
              <div className="w-2 h-2 rounded-full bg-red-400" />
              <div className="w-2 h-2 rounded-full bg-yellow-400" />
              <div className="w-2 h-2 rounded-full bg-green-400" />
              <span className="ml-4 tracking-[0.2em] font-bold">SYSTEM_LOG_MONITOR.LOG</span>
            </div>
            <div className="font-mono text-sm space-y-2 text-cyber-blue/80 overflow-y-auto max-h-[300px]">
              <p>[09:21:44] <span className="text-green-400">INFO</span> - Initializing BionicDelay™ Engine...</p>
              <p>[09:21:45] <span className="text-purple-400">AUTO</span> - Weight adjusted for "Morning Rhythms" (1.0x)</p>
              <p>[09:21:50] <span className="text-cyber-pink">DATA</span> - Injecting Obfuscated Padding [5.2ms]</p>
              <p>[09:22:12] <span className="text-green-400">DONE</span> - Matrix Post Sequence Complete for UID#392</p>
              <p>[09:23:01] <span className="text-blue-400">WARN</span> - Sleeping for 124s (Gaussian Var: 17.5)</p>
              <p className="animate-pulse">_</p>
            </div>
            <div className="absolute -bottom-10 -right-10 w-40 h-40 bg-cyber-blue/20 blur-[60px] -z-10" />
          </div>
        </motion.div>
      </div>
    </section>
  );
};
