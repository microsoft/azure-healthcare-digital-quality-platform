import React from 'react';

interface PatientIdProps {
  name?: string;
  dob?: string;
  gender?: string;
  mrn?: string;
  admit_date?: string;
  site?: string;
  bed?: string;
  onSwitchPatient?: () => void;
}

export const PatientId: React.FC<PatientIdProps> = ({
  name,
  dob,
  gender,
  mrn,
  admit_date,
  site,
  bed,
  onSwitchPatient,
}) => {
  // Calculate age from date of birth
  const calculateAge = (dob: string | undefined) => {
    if (!dob || dob.length !== 8) {
      return 'N/A';
    }
    
    try {
      const birthDate = new Date(
        parseInt(dob.substring(0, 4)), // year
        parseInt(dob.substring(4, 6)) - 1, // month (0-indexed)
        parseInt(dob.substring(6, 8)) // day
      );
      const today = new Date();
      let age = today.getFullYear() - birthDate.getFullYear();
      const monthDiff = today.getMonth() - birthDate.getMonth();
      if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
        age--;
      }
      return age;
    } catch (error) {
      return 'N/A';
    }
  };

  const formatDate = (dateStr: string | undefined) => {
    if (!dateStr) return 'N/A';
    
    if (dateStr.length === 8) {
      // Format YYYYMMDD to MM/DD/YYYY
      try {
        return `${dateStr.substring(4, 6)}/${dateStr.substring(6, 8)}/${dateStr.substring(0, 4)}`;
      } catch (error) {
        return dateStr;
      }
    }
    return dateStr; // Return as-is if already formatted
  };

  const calculateDaysSinceAdmission = (admitDate: string | undefined) => {
    //console.log('Admit date input:', admitDate); // Debug log
    
    if (!admitDate) {
      return 'XXX';
    }
    
    try {
      let year, month, day;
      
      // Handle different date formats
      if (admitDate.length === 8) {
        // YYYYMMDD format
        year = parseInt(admitDate.substring(0, 4));
        month = parseInt(admitDate.substring(4, 6)) - 1; // month (0-indexed)
        day = parseInt(admitDate.substring(6, 8));
      } else if (admitDate.includes('-')) {
        // YYYY-MM-DD format
        const parts = admitDate.split('-');
        if (parts.length === 3) {
          year = parseInt(parts[0]);
          month = parseInt(parts[1]) - 1; // month (0-indexed)
          day = parseInt(parts[2]);
        } else {
          return 'XXX';
        }
      } else if (admitDate.includes('/')) {
        // MM/DD/YYYY format
        const parts = admitDate.split('/');
        if (parts.length === 3) {
          month = parseInt(parts[0]) - 1; // month (0-indexed)
          day = parseInt(parts[1]);
          year = parseInt(parts[2]);
        } else {
          return 'XXX';
        }
      } else {
        return 'XXX';
      }
      
      // Validate parsed values
      if (isNaN(year) || isNaN(month) || isNaN(day)) {
        //console.log('Invalid date components:', { year, month, day });
        return 'XXX';
      }
      
      // Create admission date
      const admission = new Date(year, month, day);
      //console.log('Parsed admission date:', admission);
      
      // Get current date in Eastern timezone
      const now = new Date();
      const easternNow = new Date(now.toLocaleString("en-US", {timeZone: "America/New_York"}));
      //console.log('Current Eastern date:', easternNow);
      
      // Reset time to midnight for accurate day calculation
      admission.setHours(0, 0, 0, 0);
      easternNow.setHours(0, 0, 0, 0);
      
      const diffTime = easternNow.getTime() - admission.getTime();
      const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));
      
      //console.log('Days difference:', diffDays);
      return Math.max(0, diffDays);
    } catch (error) {
      console.error('Error calculating days since admission:', error);
      return 'XXX';
    }
  };

  return (
    <div className="bg-gray-100 rounded-lg p-4 pl-0 h-full flex flex-col">
      <div className="flex flex-wrap items-center justify-between mb-2 pl-4">
        <div className="text-lg font-semibold text-gray-800">
          {name || 'FirstName, LastName'}
        </div>
        <button 
          onClick={onSwitchPatient}
          className="switch-patient-button flex items-center gap-2"
        >
          Switch Patient
          <svg 
            width="32" 
            height="32" 
            viewBox="0 0 24 24" 
            fill="none" 
            stroke="currentColor" 
            strokeWidth="1"
          >
            <path d="M17 12H7M7 12l3 3M7 12l3-3M17 12l-3 3M17 12l-3-3"/>
          </svg>
        </button>
      </div>
      
      <div className="grid grid-cols-2 gap-8 flex-1 pl-4">
        <div className="space-y-1">
          <div className="text-sm text-gray-600">
            {gender || 'N/A'}, {calculateAge(dob)} y.o.
          </div>
          <div className="text-sm text-gray-600">
            Hospital: {site || 'XXX-XXXX-X'}
          </div>
          <div className="text-sm text-gray-600">
            Bed: {bed || 'XXXX / X'}
          </div>
        </div>
        
        <div className="space-y-1">
          <div className="text-sm text-gray-600">
            Member Id: {mrn || 'XXXXXXX'}
          </div>
          <div className="text-sm text-gray-600">
            Admitted: {formatDate(admit_date) || 'MM/DD/YYYY'} ({calculateDaysSinceAdmission(admit_date)}D)
          </div>
        </div>
      </div>
    </div>
  );
};

export default PatientId;