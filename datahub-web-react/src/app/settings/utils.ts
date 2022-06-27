import { AccessTokenDuration, AccessTokenType } from '../../types.generated';

/** A type of DataHub Access Token. */
export const ACCESS_TOKEN_TYPES = [{ text: 'Personal', type: AccessTokenType.Personal }];

/** The duration for which an Access Token is valid. */
export const ACCESS_TOKEN_DURATIONS = [
    { text: '1 hour', duration: AccessTokenDuration.OneHour },
    { text: '1 day', duration: AccessTokenDuration.OneDay },
    { text: '1 month', duration: AccessTokenDuration.OneMonth },
    { text: '3 months', duration: AccessTokenDuration.ThreeMonths },
];

const addHours = (hour: number) => {
    const result = new Date();
    result.setHours(result.getHours() + hour);
    return `The token will expire on ${result.toLocaleDateString()} at ${result.toLocaleTimeString()}.`;
};

const addDays = (days: number) => {
    const result = new Date();
    result.setDate(result.getDate() + days);
    return `The token will expire on ${result.toLocaleDateString()} at ${result.toLocaleTimeString()}.`;
};

const addMonths = (month: number) => {
    const result = new Date();
    result.setMonth(result.getMonth() + month);
    return `The token will expire on ${result.toLocaleDateString()} at ${result.toLocaleTimeString()}.`;
};

export const getTokenExpireDate = (duration: AccessTokenDuration) => {
    switch (duration) {
        case AccessTokenDuration.OneHour:
            return addHours(1);
        case AccessTokenDuration.OneDay:
            return addDays(1);
        case AccessTokenDuration.OneMonth:
            return addMonths(1);
        case AccessTokenDuration.ThreeMonths:
            return addMonths(3);
        default:
            return AccessTokenDuration.OneMonth;
    }
};
