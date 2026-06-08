import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import { IPatientCollection } from ".";

const initialState: IPatientCollection = {
  current: 0,
  patients: [],
  currentPatient: "",
};

const patientSlice = createSlice({
  name: "patient",
  initialState,
  reducers: {
    addPatient: (state, action: PayloadAction<string>) => {
      state.patients.push(action.payload);
      state.current = state.patients.length - 1;
      state.currentPatient = action.payload;
    },
    clearPatients: () => {
      return initialState;
    },
    setCurrentPatient: (state, action: PayloadAction<number>) => {
      if (action.payload < 0 || action.payload >= state.patients.length) {
        return;
      }
      state.current = action.payload;
      state.currentPatient = state.patients[action.payload];
    },
    addToCurrentPatient: (state, action: PayloadAction<string>) => {
      state.patients[state.current] += action.payload;
      state.currentPatient = state.patients[state.current];
    },
  },
});

export const { addPatient, clearPatients, setCurrentPatient, addToCurrentPatient } =
  patientSlice.actions;
export default patientSlice.reducer;
