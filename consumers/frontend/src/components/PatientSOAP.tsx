import React, { useState, useRef, useEffect, useMemo } from 'react';

// A single SOAP data entry possibly associated with a role
interface SOAPEntry {
  subjective?: string;
  objective?: string;
  assessment?: string;
  plan?: string;
  role?: string; // e.g. physician, nurse, case worker
}

// Multi-round shape: { "1": [ { role:"physician", ... }, { role:"nurse", ... } ], "2": [ ... ] }
// Backward compatibility: a legacy single-round object that already has the SOAP fields.
type RoundsInput = Record<string, SOAPEntry[] | SOAPEntry> | SOAPEntry;

interface PatientSOAPProps {
  rounds: RoundsInput; // Accept legacy or new structure
  initialRound?: string;
}

export const PatientSOAP: React.FC<PatientSOAPProps> = ({ rounds, initialRound }) => {
  const [activeTab, setActiveTab] = useState<'subjective' | 'objective' | 'assessment' | 'plan'>('subjective');
  const containerRef = useRef<HTMLDivElement>(null);
  const tabNavRef = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({
    subjective: null,
    objective: null,
    assessment: null,
    plan: null,
  });

  // Normalize input into a rounds map of roundKey -> SOAPEntry[]
  const normalizedRounds: Record<string, SOAPEntry[]> = useMemo(() => {
    if (!rounds) return {};
    // Legacy: single object with soap fields
    const possibleSingle = rounds as SOAPEntry;
    const soapKeys = ['subjective', 'objective', 'assessment', 'plan'];
    const isSingle = soapKeys.some(k => (possibleSingle as any)[k] !== undefined) && !Object.keys(rounds as any).some(k => /^\d+$/.test(k));
    if (isSingle) {
      return { '1': [possibleSingle] };
    }
    const result: Record<string, SOAPEntry[]> = {};
    Object.entries(rounds as Record<string, SOAPEntry[] | SOAPEntry>).forEach(([k, v]) => {
      if (!v) return;
      if (Array.isArray(v)) {
        result[k] = v;
      } else {
        result[k] = [v];
      }
    });
    return result;
  }, [rounds]);

  const roundKeys = useMemo(() => Object.keys(normalizedRounds).sort((a, b) => Number(a) - Number(b)), [normalizedRounds]);
  const [selectedRound, setSelectedRound] = useState<string>(() => initialRound && normalizedRounds[initialRound] ? initialRound : (roundKeys[0] || ''));

  // When rounds change, ensure selectedRound is valid
  useEffect(() => {
    if (!selectedRound || !normalizedRounds[selectedRound]) {
      setSelectedRound(roundKeys[0] || '');
    }
  }, [normalizedRounds, roundKeys, selectedRound]);

  const currentEntries: SOAPEntry[] = useMemo(() => {
    return selectedRound ? normalizedRounds[selectedRound] || [] : [];
  }, [normalizedRounds, selectedRound]);

  // Role handling (phase 2)
  const allRoles = useMemo(() => {
    const set = new Set<string>();
    currentEntries.forEach(e => {
      if (e.role) set.add(e.role);
    });
    return Array.from(set.values()).sort();
  }, [currentEntries]);

  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());
  // Reset role selection when round changes
  useEffect(() => {
    setSelectedRoles(new Set(allRoles));
  }, [allRoles, selectedRound]);

  const toggleRole = (role: string) => {
    setSelectedRoles(prev => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role); else next.add(role);
      if (next.size === 0) {
        // Prevent empty selection -> re-enable all
        return new Set(allRoles);
      }
      return next;
    });
  };

  const showAllRolesSelected = selectedRoles.size === allRoles.length;
  const toggleAllRoles = () => {
    if (showAllRolesSelected) {
      // Collapse to first role to demonstrate filtering
      if (allRoles[0]) setSelectedRoles(new Set([allRoles[0]]));
    } else {
      setSelectedRoles(new Set(allRoles));
    }
  };

  if (!rounds || roundKeys.length === 0) {
    return null;
  }

  const tabs = [
    { key: 'subjective' as const, label: 'Subjective' },
    { key: 'objective' as const, label: 'Objective' },
    { key: 'assessment' as const, label: 'Assessment' },
    { key: 'plan' as const, label: 'Plan' },
  ];

  const renderSectionContent = (sectionKey: keyof SOAPEntry) => {
    // Filter by selected roles (if roles exist) otherwise show all
    let entries = currentEntries;
    if (allRoles.length > 0 && selectedRoles.size > 0) {
      entries = entries.filter(e => e.role ? selectedRoles.has(e.role) : true);
    }
    if (entries.length === 0) return <p className="text-sm text-gray-500">No data available</p>;
    return (
      <div className="space-y-4">
        {entries.map((e, idx) => {
          const content = e[sectionKey];
          if (!content) return null;
          return (
            <div key={e.role || idx} className="rounded border border-gray-100 p-3 bg-white/50">
              {e.role && allRoles.length > 0 && <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{e.role}</div>}
              <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{content}</p>
            </div>
          );
        })}
      </div>
    );
  };

  useEffect(() => {
    const handleScroll = () => {
      if (!containerRef.current) return;
      const container = containerRef.current;
      const scrollTop = container.scrollTop;
      const containerHeight = container.clientHeight;
      const scrollHeight = container.scrollHeight;
      if (scrollTop + containerHeight >= scrollHeight - 10) {
        setActiveTab('plan');
        return;
      }
      let mostVisibleSection: typeof activeTab = 'subjective';
      let maxVisibility = 0;
      tabs.forEach((tab) => {
        const element = sectionRefs.current[tab.key];
        if (element) {
          const rect = element.getBoundingClientRect();
          const containerRect = container.getBoundingClientRect();
          const elementTop = rect.top - containerRect.top;
          const visibleTop = Math.max(0, -elementTop);
          const visibleBottom = Math.min(rect.height, containerHeight - elementTop);
          const visibleHeight = Math.max(0, visibleBottom - visibleTop);
          const visibilityRatio = visibleHeight / rect.height;
          if (visibilityRatio > maxVisibility) {
            maxVisibility = visibilityRatio;
            mostVisibleSection = tab.key;
          }
        }
      });
      if (mostVisibleSection !== activeTab) {
        setActiveTab(mostVisibleSection);
      }
    };
    const container = containerRef.current;
    if (container) {
      container.addEventListener('scroll', handleScroll);
      return () => container.removeEventListener('scroll', handleScroll);
    }
  }, [activeTab, tabs]);

  // Scroll tab navigation horizontally when active tab changes
  useEffect(() => {
    if (tabNavRef.current) {
      const activeButton = tabNavRef.current.querySelector(`[data-tab="${activeTab}"]`) as HTMLElement;
      if (activeButton) {
        activeButton.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      }
    }
  }, [activeTab]);

  const scrollToSection = (sectionKey: string) => {
    const element = sectionRefs.current[sectionKey];
    if (element && containerRef.current) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  return (
    <div className="mb-6">
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {/* Round & Role selectors + Tab Navigation */}
        <div className="sticky top-0 bg-white z-10 border-b border-gray-200">
          <div className="p-3 pb-0 space-y-3">
            {roundKeys.length > 1 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Rounds:</span>
                {roundKeys.map(rk => (
                  <button
                    key={rk}
                    onClick={() => setSelectedRound(rk)}
                    className={`px-2 py-1 text-xs rounded border ${selectedRound === rk ? 'bg-indigo-50 border-indigo-400 text-indigo-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
                  >
                    {rk}
                  </button>
                ))}
              </div>
            )}
            {allRoles.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Roles:</span>
                <button
                  onClick={toggleAllRoles}
                  className={`px-2 py-1 text-xs rounded border ${showAllRolesSelected ? 'bg-indigo-50 border-indigo-400 text-indigo-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
                >All</button>
                {allRoles.map(role => (
                  <button
                    key={role}
                    onClick={() => toggleRole(role)}
                    className={`px-2 py-1 text-xs rounded border ${selectedRoles.has(role) ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
                  >{role}</button>
                ))}
              </div>
            )}
          </div>
          <nav ref={tabNavRef} className="flex pl-0 overflow-x-auto scrollbar-hide mt-3">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                data-tab={tab.key}
                onClick={() => scrollToSection(tab.key)}
                className={`px-4 sm:px-6 py-3 text-sm font-bold border-b-2 transition-colors whitespace-nowrap flex-shrink-0 ${
                  activeTab === tab.key
                    ? 'border-gray-400 bg-gray-50'
                    : 'border-transparent hover:border-gray-300'
                }`}
                style={{ color: '#21206F' }}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
        
        {/* Scrollable Content */}
        <div 
          ref={containerRef}
          className="h-96 overflow-y-auto"
        >
          {tabs.map((tab) => (
            <div
              key={tab.key}
              ref={(el) => (sectionRefs.current[tab.key] = el)}
              className="p-6 border-b border-gray-100 last:border-b-0 min-h-[200px]"
            >
              <h4 className="font-semibold text-gray-600 mb-3">
                {tab.label}
              </h4>
              {renderSectionContent(tab.key)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default PatientSOAP;
