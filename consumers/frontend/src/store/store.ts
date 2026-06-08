import { Action, configureStore, ThunkAction } from "@reduxjs/toolkit";
import patientReducer from "./patientSlice";

export const store = configureStore({
  reducer: {
    patient: patientReducer,
  },
});

export type AppDispatch = typeof store.dispatch;
export type RootState = ReturnType<typeof store.getState>;
export type AppThunk<ReturnType = void> = ThunkAction<
  ReturnType,
  RootState,
  unknown,
  Action<string>
>;
